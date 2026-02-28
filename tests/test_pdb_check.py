"""Tests for check_pdb_upgrade_risk tool handler."""

# Note 1: `from __future__ import annotations` enables PEP 604-style union types
# (e.g., `int | None`) as type hints on Python 3.9 and older. Without this import
# those annotations would raise a TypeError at class-definition time on older
# runtimes. This is a backwards-compatibility shim that is safe to include
# unconditionally.
from __future__ import annotations

# Note 2: `unittest.mock` is the standard-library mocking framework. It is
# preferred over third-party libraries (e.g., pytest-mock's `mocker`) when you
# want tests to have zero extra dependencies. `AsyncMock` (added in Python 3.8)
# is specifically designed for coroutine functions: calling it returns an
# awaitable, so `await mock_obj.method()` works without extra configuration.
# `patch` is a context-manager / decorator that temporarily replaces a named
# object in a module's namespace for the duration of the test.
from unittest.mock import AsyncMock, patch

# Note 3: Importing the handler function directly (rather than the module) is
# the recommended pattern when you only need to test a single entry-point.
# It also makes assertions more readable because the symbol name in the test
# matches exactly what the production call site looks like.
from platform_mcp_server.tools.pdb_check import check_pdb_risk_handler


# Note 4: Builder / factory functions (named with a leading underscore to signal
# "private to this module") are a key pytest idiom for test data construction.
# They centralise the shape of the data structure and provide safe defaults so
# each test only needs to supply the fields that are actually relevant to its
# scenario. This dramatically reduces boilerplate and makes the intent of each
# test obvious at a glance.
def _make_pdb(
    name: str = "my-pdb",
    namespace: str = "default",
    # Note 5: `int | None` (union with None) is used here instead of
    # `Optional[int]` because the PEP 604 form is more concise and the
    # `from __future__ import annotations` import at the top makes it valid
    # on all supported Python versions. `None` as a default communicates that
    # the field is intentionally absent from the PDB spec (e.g., a PDB may
    # configure *either* maxUnavailable *or* minAvailable, not both).
    max_unavailable: int | None = None,
    min_available: int | None = None,
    # Note 6: `disruptions_allowed=0` is the most restrictive value. Starting
    # with the tightest constraint as a default forces each test to explicitly
    # opt in to a "safe" configuration, making accidental green tests less
    # likely. A disruptions_allowed of 0 means the PDB is currently blocking
    # all voluntary disruptions such as node drains.
    disruptions_allowed: int = 0,
    current_healthy: int = 3,
    expected_pods: int = 3,
    selector: dict | None = None,
) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "max_unavailable": max_unavailable,
        "min_available": min_available,
        "current_healthy": current_healthy,
        # Note 7: `desired_healthy` is intentionally set equal to
        # `current_healthy` here. When a PDB is in a steady state the
        # desired count matches the current count. Tests that want to model a
        # degraded cluster should override `current_healthy` to something lower
        # to create a gap between desired and current, which the handler uses
        # when computing disruption budgets.
        "desired_healthy": current_healthy,
        "disruptions_allowed": disruptions_allowed,
        "expected_pods": expected_pods,
        # Note 8: `selector or {"app": "my-app"}` uses Python's short-circuit
        # evaluation to provide a non-empty default selector dict. An empty
        # selector `{}` on a PDB would match *all* pods in the namespace, which
        # is rarely the intent in tests. Using a named selector keeps each
        # synthetic PDB scoped to a recognisable fake workload.
        "selector": selector or {"app": "my-app"},
    }


# Note 9: A second factory function for node objects mirrors the PDB factory
# above. Separating the two keeps each factory focused on a single resource
# type and makes it easy to add new fields (e.g., node taints) in one place
# without updating every test that creates a node.
def _make_node(name: str, pool: str = "userpool", unschedulable: bool = False) -> dict:
    return {
        "name": name,
        "pool": pool,
        # Note 10: Pinning the Kubernetes version to a concrete string like
        # "v1.29.8" (not "latest" or a variable) makes tests deterministic and
        # version-independent. If the handler ever uses the version string for
        # comparisons the test will catch regressions because the expected value
        # is explicit.
        "version": "v1.29.8",
        "unschedulable": unschedulable,
        # Note 11: CPU is expressed in millicores ("4000m") and memory in binary
        # gigabytes ("16Gi") because that is how the Kubernetes API returns
        # allocatable resources. Using realistic unit strings exercises any
        # parsing logic in the handler rather than bypassing it with plain
        # integers.
        "allocatable_cpu": "4000m",
        "allocatable_memory": "16Gi",
        # Note 12: `conditions: {"Ready": "True"}` mirrors the Kubernetes node
        # condition map. The string "True" (not the boolean True) is intentional:
        # the Kubernetes API serialises condition statuses as the strings "True",
        # "False", or "Unknown". Tests that use the boolean True would pass when
        # a truthiness check is used but fail when the handler does an exact
        # string comparison — a subtle bug that realistic test data prevents.
        "conditions": {"Ready": "True"},
        "labels": {"agentpool": pool},
    }


# Note 13: Grouping related tests into classes is a standard pytest organisational
# pattern. Class names describe the scenario or feature being tested (here, the
# "mode" parameter of the handler). pytest discovers and runs all methods whose
# names start with `test_` inside the class. No `__init__` is needed because
# pytest instantiates the class fresh for every test method, providing automatic
# test isolation without any teardown code.
class TestCheckPdbRiskPreflight:
    # Note 14: `async def test_*` methods work in pytest when the test suite is
    # configured with `asyncio_mode = "auto"` (set in pyproject.toml or
    # pytest.ini). In auto mode pytest-asyncio wraps every async test in an
    # event loop automatically, so you do not need the `@pytest.mark.asyncio`
    # decorator on each individual test. This keeps the test signatures clean
    # and avoids the common mistake of forgetting the decorator.
    async def test_max_unavailable_zero_flagged(self) -> None:
        # Note 15: `AsyncMock()` creates a mock whose methods return awaitables
        # by default. When `check_pdb_risk_handler` calls
        # `await mock_policy.get_pdbs()` the mock automatically returns the
        # value configured on `.return_value`. Without `AsyncMock` a plain
        # `MagicMock` would return a non-awaitable, causing an immediate
        # `TypeError` inside the handler — a confusing error that looks like a
        # bug in the production code rather than a missing mock setup.
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [_make_pdb(name="tight-pdb", max_unavailable=0)]
        # Note 16: `evaluate_pdb_satisfiability` returns a list of *blocked*
        # PDBs — those that would prevent a node drain. By returning a list
        # containing the tight PDB with a populated `block_reason`, we simulate
        # the policy client having determined that this PDB is problematic.
        # The `**` dict unpacking merges the base PDB dict with the extra
        # `block_reason` key, keeping the data consistent without duplicating
        # all the other fields.
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {**_make_pdb(name="tight-pdb", max_unavailable=0), "block_reason": "maxUnavailable=0"}
        ]
        mock_core = AsyncMock()
        # Note 17: Returning an empty list for `get_nodes` isolates this test
        # to the PDB logic only. An empty node list means the handler cannot
        # attribute any block to a specific cordoned node, so any risk that
        # surfaces must come from the PDB configuration alone. This makes the
        # assertion about `result.risks` a direct verification of the PDB
        # evaluation path.
        mock_core.get_nodes.return_value = []

        # Note 18: The `with (patch(...), patch(...)):` compound context manager
        # (available in Python 3.10+ without backslash continuation) replaces
        # the two client classes in the `pdb_check` module namespace for the
        # duration of the `with` block. Using `return_value=mock_policy` means
        # that when the handler does `K8sPolicyClient(cluster)` it receives
        # `mock_policy` instead of a real API client. Both patches are undone
        # automatically when the `with` block exits, ensuring they do not leak
        # into other tests.
        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="preflight")

        # Note 19: Asserting `len(result.risks) == 1` (not `>= 1`) is a
        # deliberate precision choice. A test that allows multiple risks could
        # hide a bug where the handler double-counts the same PDB. Exact
        # equality forces the developer to update the test if the counting logic
        # changes, making the failure signal meaningful.
        assert len(result.risks) == 1
        # Note 20: The substring check `"maxUnavailable=0" in result.risks[0].reason`
        # is intentionally loose. Testing for an exact string would make the
        # test brittle to minor wording changes (e.g., capitalisation) that do
        # not affect correctness. The substring asserts that the reason is
        # informative and identifies the root cause without over-specifying the
        # exact human-readable message format.
        assert "maxUnavailable=0" in result.risks[0].reason

    async def test_min_available_equals_ready_flagged(self) -> None:
        # Note 21: This test covers the second common PDB misconfiguration:
        # setting `minAvailable` equal to the current pod count. When all pods
        # are required to be healthy, zero disruptions are allowed and node
        # drains will block indefinitely. The magic numbers 3/3 are chosen to
        # make the equality self-evident in the test data — any reader can see
        # at a glance that minAvailable (3) equals current_healthy (3).
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            _make_pdb(name="exact-pdb", min_available=3, current_healthy=3, disruptions_allowed=0)
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {
                **_make_pdb(name="exact-pdb", min_available=3, current_healthy=3),
                "block_reason": "minAvailable=3 equals current healthy count (3)",
            }
        ]
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = []

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="preflight")

        assert len(result.risks) == 1

    async def test_available_budget_not_flagged(self) -> None:
        # Note 22: This is a "happy path" or "negative" test — it verifies that
        # the handler does NOT raise a false alarm when the PDB is configured
        # correctly. `min_available=2` with `current_healthy=4` and
        # `disruptions_allowed=2` is a textbook safe configuration: two pods
        # can be disrupted before the PDB would block. Negative tests are
        # essential because a handler that always returns risks would pass every
        # positive test while being completely broken.
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            _make_pdb(name="safe-pdb", min_available=2, current_healthy=4, disruptions_allowed=2)
        ]
        # Note 23: Returning an empty list from `evaluate_pdb_satisfiability`
        # simulates the policy client concluding that all PDBs have sufficient
        # budget and none will block a drain. The handler should propagate this
        # empty-blocked-list result to `result.risks` without adding any
        # phantom entries.
        mock_policy.evaluate_pdb_satisfiability.return_value = []
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = []

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="preflight")

        assert len(result.risks) == 0


# Note 24: Separating "preflight" and "live" scenarios into distinct test
# classes documents that these are two distinct execution modes of the same
# handler with different semantics. Preflight mode evaluates hypothetical risk
# before a drain; live mode inspects what is actively blocking an ongoing
# upgrade. Grouping by mode also allows pytest's `-k` flag to run only one
# class at a time during focused debugging.
class TestCheckPdbRiskLive:
    async def test_active_block_on_cordoned_node(self) -> None:
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [_make_pdb(name="blocking-pdb", max_unavailable=0, disruptions_allowed=0)]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {**_make_pdb(name="blocking-pdb", max_unavailable=0), "block_reason": "maxUnavailable=0"}
        ]
        mock_core = AsyncMock()
        # Note 25: Providing a cordoned node (`unschedulable=True`) is the key
        # differentiator for the "live" mode test. In live mode the handler is
        # expected to cross-reference blocked PDBs with nodes that are actively
        # being drained (i.e., unschedulable). A cordoned node with a blocking
        # PDB represents an upgrade that is stuck right now, not just a
        # hypothetical future risk.
        mock_core.get_nodes.return_value = [_make_node("node-1", unschedulable=True)]

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="live")

        # Note 26: `>= 1` (rather than `== 1`) is used here because live mode
        # may add additional contextual risks (e.g., one risk per blocked PDB
        # plus one for the cordoned node state). The test's primary goal is to
        # confirm that *at least one* active block is detected, not to pin the
        # exact number of risk objects produced by the live analysis logic.
        assert len(result.risks) >= 1
        # Note 27: Asserting `result.mode == "live"` verifies that the handler
        # correctly reflects the requested mode in its response object. This
        # matters because callers may use the mode field to decide how to
        # present results (e.g., showing "active block" UI vs. "pre-flight
        # warning" UI).
        assert result.mode == "live"

    async def test_no_active_blocks(self) -> None:
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            _make_pdb(name="safe-pdb", min_available=2, current_healthy=4, disruptions_allowed=2)
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = []
        mock_core = AsyncMock()
        # Note 28: Having a cordoned node with a safe PDB (disruptions_allowed=2)
        # exercises the negative path for live mode: the node is being drained
        # but the PDB has enough budget, so no risk should be reported. This is
        # important because a buggy handler might flag any cordoned node as
        # risky regardless of the PDB budget, and this test would catch that.
        mock_core.get_nodes.return_value = [_make_node("node-1", unschedulable=True)]

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="live")

        assert len(result.risks) == 0


# Note 29: The "fan-out" test class covers a different code path entirely: the
# `check_pdb_risk_all` function that iterates over every known cluster and
# calls the single-cluster handler for each one. Separating this into its own
# class makes it clear that fan-out logic is tested independently of the
# per-cluster risk evaluation logic.
class TestCheckPdbRiskFanOut:
    async def test_cluster_all_fan_out(self) -> None:
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = []

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            # Note 30: Importing `check_pdb_risk_all` *inside* the `with` block
            # is a deliberate technique used when the import itself triggers
            # module-level code that reads the symbol being patched. By deferring
            # the import until after the patches are active you guarantee that
            # any top-level references in `pdb_check` to `K8sPolicyClient` or
            # `K8sCoreClient` are already pointing at the mocks. This avoids the
            # classic "patch too late" failure mode where the real class is
            # captured by a module-level variable before the patch takes effect.
            from platform_mcp_server.tools.pdb_check import check_pdb_risk_all

            results = await check_pdb_risk_all()

        # Note 31: Asserting `len(results) == 6` encodes the expected number of
        # clusters in the platform. This is a contract test: if a new cluster is
        # added to the platform's cluster registry the test will fail with a
        # clear count mismatch, prompting the developer to also update any
        # cluster-enumeration logic. It is a lightweight alternative to mocking
        # the cluster registry itself.
        assert len(results) == 6
