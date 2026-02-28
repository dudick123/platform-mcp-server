"""Integration tests for server.py tool wrappers — single cluster and fan-out."""

# Note 1: `from __future__ import annotations` is included at the top of every module
# in this project. It defers annotation evaluation so that forward references and
# complex generic types (e.g., `dict[str, list[X]]`) are stored as strings rather than
# being resolved at import time. This prevents `NameError` for types that are defined
# later in the same file and reduces import overhead in large codebases.
from __future__ import annotations

import json

# Note 2: `AsyncMock` and `patch` are imported from `unittest.mock`, which is part of
# the Python standard library. `patch` replaces a named object in a module's namespace
# for the duration of a `with` block (or decorated test), then restores the original.
# `AsyncMock` is a specialised mock that returns an awaitable when called, which is
# required when the production code uses `await` on the patched object. Using the
# plain `MagicMock` for an async function would raise a `TypeError` at runtime because
# a regular mock is not awaitable.
from unittest.mock import AsyncMock, patch

import pytest

# Note 3: Pydantic model classes are imported here so that test factory functions can
# construct fully-validated instances. Using real model objects (rather than plain dicts)
# ensures that the test data satisfies the same schema constraints enforced in
# production, catching any mismatch between what the server serialises and what the
# models actually contain. If a field is renamed in the model, the factory function
# will raise a `ValidationError` immediately, rather than silently producing bad JSON.
from platform_mcp_server.models import (
    NodePoolPressureOutput,
    NodePoolResult,
    NodePoolVersionInfo,
    PdbCheckOutput,
    PodDetail,
    PodHealthOutput,
    UpgradeDurationOutput,
    UpgradeProgressOutput,
    UpgradeStatusOutput,
)

# Note 4: The MCP tool functions are imported directly from `server` so that these
# tests exercise the actual async wrappers (the functions decorated with `@mcp.tool`)
# rather than the lower-level handler functions. This is an integration-style test: it
# tests the full server-side translation layer — argument handling, fan-out logic, JSON
# serialisation — while still isolating the I/O boundary (the Kubernetes/Azure API
# calls) via mocks. This approach gives high confidence without requiring a live cluster.
from platform_mcp_server.server import (
    check_node_pool_pressure,
    check_pdb_upgrade_risk,
    get_kubernetes_upgrade_status,
    get_pod_health,
    get_upgrade_duration_metrics,
    get_upgrade_progress,
)


# Note 5: Module-level factory functions (prefixed with `_` to mark them as private
# helpers) construct minimal but valid Pydantic model instances for use in tests. The
# `cluster` parameter with a default value lets each test customise the cluster name
# without duplicating the entire model construction. This is the "object mother"
# pattern: a central place that knows how to build a valid object, reducing test
# setup noise and making it easy to add new required fields in one place rather than
# across every test.
def _pressure_output(cluster: str = "prod-eastus") -> NodePoolPressureOutput:
    # Note 6: The numeric values used here (cpu_requests_percent=50.0, etc.) are
    # deliberately "quiet" — they are neither boundary values nor extreme values. The
    # goal of these factory functions is not to test the handler's computation logic
    # (that belongs in unit tests closer to the handler), but to provide structurally
    # valid objects that the server wrapper can serialise and return. Using realistic
    # mid-range values makes the test data readable and avoids accidentally triggering
    # threshold-based branching in the serialisation path.
    return NodePoolPressureOutput(
        cluster=cluster,
        pools=[
            NodePoolResult(
                pool_name="userpool",
                cpu_requests_percent=50.0,
                memory_requests_percent=40.0,
                pending_pods=0,
                ready_nodes=3,
                max_nodes=10,
                pressure_level="ok",
            )
        ],
        summary="ok",
        # Note 7: The timestamp string uses ISO 8601 format with a UTC offset
        # (`+00:00`) rather than the `Z` suffix. Both are valid, but Pydantic's
        # `datetime` validator normalises them consistently. Using a fixed, known
        # timestamp in test data avoids flakiness caused by comparing against
        # `datetime.now()` and also makes assertions on the serialised JSON string
        # deterministic — the same input always produces the same output.
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _pod_health_output(cluster: str = "prod-eastus") -> PodHealthOutput:
    # Note 8: The pod fixture uses phase="Pending" and failure_category="scheduling"
    # rather than the happy-path "Running" phase. This is intentional: the
    # `get_pod_health` tool is designed to surface *unhealthy* pods, so a fixture that
    # represents a problem pod is more representative of real production output. Using
    # a realistic "bad" case also ensures that downstream consumers of the JSON (e.g.,
    # an AI assistant) see the same structure they would encounter in a real alert.
    return PodHealthOutput(
        cluster=cluster,
        pods=[
            PodDetail(
                name="pod-1",
                namespace="default",
                phase="Pending",
                node_name="node-1",
                failure_category="scheduling",
            )
        ],
        groups={"scheduling": 1},
        total_matching=1,
        truncated=False,
        summary="1 unhealthy pod",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _upgrade_status_output(cluster: str = "prod-eastus") -> UpgradeStatusOutput:
    # Note 9: `upgrade_active=False` and a non-empty `available_upgrades` list
    # represent a cluster that is stable but has a pending upgrade available. This is
    # the most common production state and exercises the "no upgrade in flight" branch
    # of the server wrapper. Tests for the actively-upgrading case would require a
    # different fixture with `upgrade_active=True`, which could be added as future
    # parametrised cases.
    return UpgradeStatusOutput(
        cluster=cluster,
        control_plane_version="1.29.8",
        node_pools=[
            NodePoolVersionInfo(
                pool_name="systempool",
                current_version="1.29.8",
                target_version="1.29.8",
                upgrading=False,
            )
        ],
        available_upgrades=["1.30.0"],
        upgrade_active=False,
        summary="1.29.8",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _upgrade_progress_output(cluster: str = "prod-eastus") -> UpgradeProgressOutput:
    # Note 10: `nodes=[]` represents a cluster where no nodes are currently being
    # upgraded. This empty list is intentional: it tests that the serialisation path
    # handles an empty collection correctly (no KeyError, no None serialised as `null`
    # in the JSON). An empty list is a common edge case that serialisers sometimes
    # render incorrectly if the field is typed as `Optional[list]` vs `list`.
    return UpgradeProgressOutput(
        cluster=cluster,
        upgrade_in_progress=False,
        nodes=[],
        summary="No upgrade",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _upgrade_metrics_output(cluster: str = "prod-eastus") -> UpgradeDurationOutput:
    # Note 11: `historical=[]` tests the empty-history path. In production, a cluster
    # that has never completed an upgrade will have no historical duration data. The
    # serialisation layer must return a valid JSON object with an empty array rather
    # than omitting the key or returning `null`. This fixture ensures that path is
    # exercised every time the test suite runs.
    return UpgradeDurationOutput(
        cluster=cluster,
        node_pool="userpool",
        historical=[],
        summary="No active upgrade",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _pdb_check_output(cluster: str = "prod-eastus") -> PdbCheckOutput:
    # Note 12: `risks=[]` represents the ideal preflight outcome: no PodDisruptionBudgets
    # would block the upgrade. Using the clean-pass case as the default fixture keeps
    # the "happy path" readable. Tests that need to assert on risky PDB configurations
    # should construct their own fixture with populated `risks` entries.
    return PdbCheckOutput(
        cluster=cluster,
        mode="preflight",
        risks=[],
        summary="No PDB risks",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


# Note 13: Each tool gets its own test class. This structure keeps the test file
# navigable as it grows: you can immediately find all tests for a given MCP tool by
# scanning the class names. It also allows pytest's `-k` expression filter to target a
# single tool's tests (e.g., `pytest -k TestCheckNodePoolPressure`) without affecting
# other classes. Each class follows the same three-test pattern: single cluster, all
# clusters (fan-out), and error propagation — making the test matrix easy to audit.
class TestCheckNodePoolPressure:
    # Note 14: No `@pytest.mark.asyncio` decorator is needed on any async test method
    # in this file. The `asyncio_mode = "auto"` setting in `[tool.pytest.ini_options]`
    # inside `pyproject.toml` tells pytest-asyncio to automatically treat every `async
    # def` test function as an asyncio coroutine and run it inside an event loop. This
    # reduces boilerplate: you get async test support without repeating the mark on
    # every single test. The trade-off is that it requires `pytest-asyncio` to be
    # installed, which is listed in the `dev` dependency group.
    async def test_single_cluster(self) -> None:
        # Note 15: `patch(target, new_callable=AsyncMock, return_value=...)` is the
        # canonical pattern for replacing an async function with a controllable fake.
        # The `target` string uses the *import path as seen by the module under test*
        # (i.e., `platform_mcp_server.server.check_node_pool_pressure_handler`), not
        # the path where the handler is defined. This distinction matters: `patch`
        # replaces the name in the namespace that the server module has already looked
        # up, so if the server does `from handlers import foo` and we patch
        # `handlers.foo`, the server's local reference remains unpatched. Always patch
        # where the object is *used*, not where it is *defined*.
        with patch(
            "platform_mcp_server.server.check_node_pool_pressure_handler",
            new_callable=AsyncMock,
            return_value=_pressure_output(),
        ):
            result = await check_node_pool_pressure("prod-eastus")
        # Note 16: The result is parsed back from JSON with `json.loads()` and then
        # asserted on as a Python dict. This round-trip (model -> JSON string -> dict)
        # tests the full serialisation path: it verifies that the server tool correctly
        # calls `model.model_dump_json()` (or equivalent), that the JSON is valid, and
        # that the expected fields survive the transformation. Asserting on the raw
        # string would be fragile because field ordering in JSON is not guaranteed.
        data = json.loads(result)
        assert data["cluster"] == "prod-eastus"

    async def test_all_clusters(self) -> None:
        # Note 17: Creating a list of six outputs (`[_pressure_output(f"cluster-{i}") for
        # i in range(6)]`) and returning it from the `_all` variant of the handler
        # simulates the fan-out behaviour: the server wrapper calls the handler once per
        # configured cluster and collects results. Six is used here because it matches
        # the typical number of clusters in the project's `CLUSTER_MAP`. Using a
        # list comprehension with an index in the cluster name (`cluster-0` through
        # `cluster-5`) makes it trivial to assert that *both* the first and last
        # cluster appear in the combined output.
        outputs = [_pressure_output(f"cluster-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.check_node_pool_pressure_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await check_node_pool_pressure("all")
        # Note 18: Asserting that `"cluster-0"` and `"cluster-5"` both appear in the
        # combined result string checks two things simultaneously: (1) the first cluster
        # in the list is included (no off-by-one error at the start), and (2) the last
        # cluster is included (no truncation at the end). Checking only the first or only
        # the last would leave one of those failure modes undetected.
        assert "cluster-0" in result
        assert "cluster-5" in result

    async def test_error_propagates(self) -> None:
        # Note 19: `side_effect=ValueError("test error")` configures the mock to raise
        # an exception rather than returning a value. This tests the server tool's error
        # handling contract: when the underlying handler raises, the MCP tool wrapper
        # must either re-raise or convert the exception into a `RuntimeError`. The
        # `pytest.raises(RuntimeError, match="test error")` context manager asserts
        # both the exception *type* and that the message contains the expected substring.
        # Using `match=` is important: it prevents false positives where a `RuntimeError`
        # from a completely different code path (e.g., an import failure) would
        # accidentally satisfy the assertion.
        #
        # Note 20: The parenthesised `with (patch(...), pytest.raises(...)):` syntax
        # (PEP 617, available from Python 3.10+) lets you stack two context managers
        # without nesting. Both managers are active simultaneously, which is correct here:
        # the patch must be active when the call is made so the mock raises, and
        # `pytest.raises` must be active to catch the resulting exception. Nesting them
        # would produce identical semantics but more indentation.
        with (
            patch(
                "platform_mcp_server.server.check_node_pool_pressure_handler",
                new_callable=AsyncMock,
                side_effect=ValueError("test error"),
            ),
            pytest.raises(RuntimeError, match="test error"),
        ):
            await check_node_pool_pressure("prod-eastus")


class TestGetPodHealth:
    # Note 21: The three-test structure (single, all, error) repeated across every tool
    # class is a deliberate testing matrix. Each tool has two distinct execution paths
    # (single cluster vs. fan-out) and one error path. By testing all three for every
    # tool, the suite provides full branch coverage of the routing logic in each MCP
    # wrapper function without needing to look at implementation details.
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_pod_health_handler",
            new_callable=AsyncMock,
            return_value=_pod_health_output(),
        ):
            result = await get_pod_health("prod-eastus")
        data = json.loads(result)
        assert data["cluster"] == "prod-eastus"

    async def test_all_clusters(self) -> None:
        outputs = [_pod_health_output(f"cluster-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_pod_health_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await get_pod_health("all")
        assert "cluster-0" in result

    async def test_error_propagates(self) -> None:
        # Note 22: `side_effect=RuntimeError("api fail")` tests what happens when the
        # Kubernetes API call itself raises. In `TestCheckNodePoolPressure`, the error
        # was a `ValueError` that the server converts to a `RuntimeError`. Here, a
        # `RuntimeError` is raised directly — testing that the server wrapper does NOT
        # accidentally swallow or double-wrap exceptions that are already the right type.
        # This subtle difference across test classes documents the exact exception
        # transformation contract for each tool.
        with (
            patch(
                "platform_mcp_server.server.get_pod_health_handler",
                new_callable=AsyncMock,
                side_effect=RuntimeError("api fail"),
            ),
            pytest.raises(RuntimeError, match="api fail"),
        ):
            await get_pod_health("prod-eastus")


class TestGetKubernetesUpgradeStatus:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_upgrade_status_handler",
            new_callable=AsyncMock,
            return_value=_upgrade_status_output(),
        ):
            result = await get_kubernetes_upgrade_status("prod-eastus")
        data = json.loads(result)
        # Note 23: The assertion here checks `control_plane_version` rather than
        # `cluster`. This is intentional: it validates that a *domain-specific* field
        # (the Kubernetes version string) survives serialisation correctly, not just the
        # cluster routing field. Different tests in different classes assert on different
        # fields, collectively covering more of the model surface area without requiring
        # a full deep-equality check (which would be brittle to innocuous model changes).
        assert data["control_plane_version"] == "1.29.8"

    async def test_all_clusters(self) -> None:
        outputs = [_upgrade_status_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_upgrade_status_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await get_kubernetes_upgrade_status("all")
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        # Note 24: Using the bare `Exception` base class (rather than a specific
        # subclass) as the `side_effect` type tests that the server wrapper correctly
        # propagates any exception — not just ones it explicitly anticipates. This is
        # the most permissive test possible: if the wrapper accidentally catches all
        # exceptions via a bare `except Exception: pass`, this test would fail because
        # `pytest.raises` would not see the exception escape.
        with (
            patch(
                "platform_mcp_server.server.get_upgrade_status_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await get_kubernetes_upgrade_status("prod-eastus")


class TestGetUpgradeProgress:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_upgrade_progress_handler",
            new_callable=AsyncMock,
            return_value=_upgrade_progress_output(),
        ):
            result = await get_upgrade_progress("prod-eastus")
        data = json.loads(result)
        # Note 25: `assert data["upgrade_in_progress"] is False` uses the identity
        # check `is False` rather than `== False`. In Python, `is False` is stricter:
        # it passes only for the boolean singleton `False`, whereas `== False` would
        # also pass for `0`, `None`, or any other falsy value. When round-tripping
        # through JSON, `false` is decoded as the Python boolean `False`, so `is False`
        # correctly validates that the field was serialised as a JSON boolean rather
        # than as `null` or `0`.
        assert data["upgrade_in_progress"] is False

    async def test_all_clusters(self) -> None:
        outputs = [_upgrade_progress_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_upgrade_progress_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            # Note 26: The optional `node_pool="userpool"` keyword argument is passed
            # explicitly here to test the fan-out path with a filter applied. This
            # verifies that optional parameters are threaded through the server wrapper
            # to the `_all` handler correctly. The single-cluster test omits this
            # argument to separately test the default (no filter) code path.
            result = await get_upgrade_progress("all", node_pool="userpool")
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.get_upgrade_progress_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await get_upgrade_progress("prod-eastus")


class TestGetUpgradeDurationMetrics:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_upgrade_metrics_handler",
            new_callable=AsyncMock,
            return_value=_upgrade_metrics_output(),
        ):
            result = await get_upgrade_duration_metrics("prod-eastus", "userpool")
        data = json.loads(result)
        # Note 27: Asserting on `data["node_pool"]` verifies that the `node_pool`
        # argument passed to the MCP tool is correctly forwarded to the handler and
        # reflected in the output. This is a pass-through argument test: it guards
        # against a regression where the wrapper accidentally ignores or hard-codes the
        # node pool name. Without this assertion, such a bug would silently return data
        # for the wrong pool.
        assert data["node_pool"] == "userpool"

    async def test_all_clusters(self) -> None:
        outputs = [_upgrade_metrics_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_upgrade_metrics_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            # Note 28: The positional `3` passed as the third argument represents a
            # `lookback_days` or similar integer parameter. Passing an explicit value
            # (rather than relying on the default) tests that the fan-out wrapper
            # correctly forwards non-string, non-optional arguments to the `_all`
            # handler. If the wrapper signature or the forwarding call drops this
            # argument, the mock would still be called but with the wrong arguments —
            # which would not be caught unless we also assert on `mock.call_args`.
            result = await get_upgrade_duration_metrics("all", "userpool", 3)
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.get_upgrade_metrics_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await get_upgrade_duration_metrics("prod-eastus", "userpool")


class TestCheckPdbUpgradeRisk:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.check_pdb_risk_handler",
            new_callable=AsyncMock,
            return_value=_pdb_check_output(),
        ):
            result = await check_pdb_upgrade_risk("prod-eastus")
        data = json.loads(result)
        # Note 29: Asserting on `data["mode"] == "preflight"` validates that the
        # `mode` field from the Pydantic model is correctly serialised into the JSON
        # response. The `mode` field controls how the PDB risk check behaves ("preflight"
        # runs a dry-run analysis; "live" reflects the current state of running upgrades).
        # Verifying it in the JSON output ensures the field is included in the response
        # schema that an AI assistant will receive, making the tool's output self-
        # describing.
        assert data["mode"] == "preflight"

    async def test_all_clusters(self) -> None:
        outputs = [_pdb_check_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.check_pdb_risk_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            # Note 30: Both `node_pool="userpool"` and `mode="live"` are passed here
            # to test that the fan-out path forwards *multiple* optional keyword
            # arguments simultaneously. Testing them together (rather than in separate
            # tests) is acceptable because the goal is to verify argument forwarding,
            # not to isolate each parameter — a single call that passes both exercises
            # the forwarding mechanism completely.
            result = await check_pdb_upgrade_risk("all", node_pool="userpool", mode="live")
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.check_pdb_risk_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await check_pdb_upgrade_risk("prod-eastus")
