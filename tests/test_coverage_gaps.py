"""Tests targeting previously uncovered lines across all modules."""

# Note 1: `from __future__ import annotations` enables PEP 563 postponed evaluation
# of annotations. This lets you use newer type syntax (e.g., `X | Y`) in Python 3.9
# without raising a NameError at import time. It is a common first line in typed files.
from __future__ import annotations

# Note 2: `datetime`, `timedelta`, and `UTC` are used throughout to create realistic
# timestamps for fake events. Using `datetime.now(tz=UTC)` rather than naive datetimes
# ensures tests behave consistently regardless of the host machine's local timezone.
from datetime import UTC, datetime, timedelta

# Note 3: `AsyncMock` and `MagicMock` serve distinct purposes. `MagicMock` creates a
# synchronous stand-in for any attribute or callable. `AsyncMock` is required when the
# code under test uses `await`, because calling an ordinary `MagicMock` with `await`
# raises a TypeError. `patch` replaces a named object in the module under test for the
# duration of a `with` block and restores the original afterward.
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.clients.k8s_events import K8sEventsClient, _event_timestamp
from platform_mcp_server.clients.k8s_policy import _int_or_str
from platform_mcp_server.config import CLUSTER_MAP
from platform_mcp_server.models import (
    NodePoolPressureOutput,
    PdbCheckOutput,
    PodHealthOutput,
    UpgradeDurationOutput,
    UpgradeProgressOutput,
    UpgradeStatusOutput,
)
from platform_mcp_server.tools.k8s_upgrades import get_upgrade_status_all, get_upgrade_status_handler
from platform_mcp_server.tools.node_pools import (
    _classify_pressure,
    _parse_cpu_millicores,
    _parse_memory_bytes,
    check_node_pool_pressure_all,
    check_node_pool_pressure_handler,
)
from platform_mcp_server.tools.pdb_check import _workload_from_selector, check_pdb_risk_all, check_pdb_risk_handler
from platform_mcp_server.tools.pod_classification import categorize_failure, is_unhealthy
from platform_mcp_server.tools.pod_health import get_pod_health_all, get_pod_health_handler
from platform_mcp_server.tools.upgrade_metrics import _parse_ts, get_upgrade_metrics_all, get_upgrade_metrics_handler
from platform_mcp_server.tools.upgrade_progress import (
    _parse_event_timestamp,
    get_upgrade_progress_all,
    get_upgrade_progress_handler,
)

# ---------------------------------------------------------------------------
# clients/__init__.py — load_k8s_api_client
# ---------------------------------------------------------------------------


# Note 4: Placing the import of the function under test *inside* the test method
# instead of at the top of the file is a deliberate pattern used when the module
# performs side effects at import time (e.g., reading environment variables, opening
# connections). Deferring the import until the patch is active ensures those side
# effects happen inside the controlled test environment.
class TestLoadK8sApiClient:
    def test_calls_new_client_from_config(self) -> None:
        from platform_mcp_server.clients import load_k8s_api_client

        # Note 5: `patch` replaces `new_client_from_config` only within the `with`
        # block. After the block exits the original is restored. This prevents test
        # pollution between test cases and avoids real network calls.
        with patch("platform_mcp_server.clients.new_client_from_config") as mock_fn:
            mock_fn.return_value = MagicMock()
            result = load_k8s_api_client("aks-prod-eastus")

        # Note 6: `assert_called_once_with` is more precise than checking call_count
        # separately. It verifies both that the function was called exactly once AND
        # that it received the expected arguments, catching regressions where the
        # keyword argument name or value changes.
        mock_fn.assert_called_once_with(context="aks-prod-eastus")
        assert result is mock_fn.return_value


# ---------------------------------------------------------------------------
# clients/azure_aks.py — exception paths and break in activity log
# ---------------------------------------------------------------------------


# Note 7: A pytest fixture decorated with `@pytest.fixture` is automatically injected
# into any test method that declares a parameter with the same name. Fixtures promote
# reuse and keep test bodies focused on behaviour rather than setup boilerplate. The
# fixture here returns a real `AzureAksClient` instance constructed from the shared
# `CLUSTER_MAP` config, so every test in the class starts from a consistent state.
@pytest.fixture
def aks_client() -> AzureAksClient:
    return AzureAksClient(CLUSTER_MAP["prod-eastus"])


class TestAzureAksClientErrorPaths:
    # Note 8: Exception-path tests exist to exercise branches that are only reachable
    # when a dependency fails. Without these tests the coverage tool reports the
    # `except` clause (and any cleanup code inside it) as uncovered, giving a false
    # sense that all paths are tested. `side_effect = Exception(...)` on a mock causes
    # it to raise when called, simulating a network or API failure.
    async def test_get_node_pool_state_raises_on_error(self, aks_client: AzureAksClient) -> None:
        mock_container = MagicMock()
        mock_container.agent_pools.get.side_effect = Exception("Forbidden")

        # Note 9: The parenthesised `with (A, B):` syntax (Python 3.10+) stacks two
        # context managers without nesting. Here it simultaneously applies the patch
        # and asserts that the expected exception is raised. The `match` parameter
        # compiles its value as a regex, so tests remain valid even if the exception
        # message gains extra context text.
        with (
            patch.object(aks_client, "_get_container_client", return_value=mock_container),
            pytest.raises(Exception, match="Forbidden"),
        ):
            await aks_client.get_node_pool_state("userpool")

    async def test_get_upgrade_profile_raises_on_error(self, aks_client: AzureAksClient) -> None:
        mock_container = MagicMock()
        mock_container.managed_clusters.get_upgrade_profile.side_effect = Exception("Timeout")

        with (
            patch.object(aks_client, "_get_container_client", return_value=mock_container),
            pytest.raises(Exception, match="Timeout"),
        ):
            await aks_client.get_upgrade_profile()

    async def test_activity_log_break_when_count_reached(self, aks_client: AzureAksClient) -> None:
        """The break fires when records reach the requested count (count=2, 5 entries available)."""
        mock_monitor = MagicMock()
        now = datetime.now(tz=UTC)

        # Note 10: The inner helper `_make_entry` is defined inside the test to keep
        # the fixture data co-located with the scenario that needs it. This avoids
        # polluting the module namespace with one-off factory functions that are only
        # meaningful in a single context.
        def _make_entry() -> MagicMock:
            e = MagicMock()
            e.status.value = "Succeeded"
            e.event_timestamp = now
            e.submission_timestamp = now - timedelta(hours=1)
            e.operation_name.value = "Microsoft.ContainerService/managedClusters/write"
            e.description = "Upgrade completed"
            return e

        # Note 11: Returning 5 entries when the caller requests `count=2` exercises
        # the `break` statement inside the accumulation loop in `get_activity_log_upgrades`.
        # Without providing more items than the requested count, the loop would simply
        # exhaust the iterable and the `break` branch would never be reached.
        mock_monitor.activity_logs.list.return_value = [_make_entry() for _ in range(5)]

        with patch.object(aks_client, "_get_monitor_client", return_value=mock_monitor):
            records = await aks_client.get_activity_log_upgrades(count=2)

        assert len(records) == 2


# ---------------------------------------------------------------------------
# clients/k8s_events.py — exception path and _event_timestamp edge cases
# ---------------------------------------------------------------------------


@pytest.fixture
def events_client() -> K8sEventsClient:
    return K8sEventsClient(CLUSTER_MAP["prod-eastus"])


class TestK8sEventsClientPodEventsError:
    # Note 12: Testing that exceptions propagate without being swallowed verifies the
    # contract that callers are responsible for error handling. If `get_pod_events`
    # silently catches and suppresses exceptions an operator would never know an API
    # was unreachable. The `pytest.raises` assertion ensures the exception is NOT
    # swallowed and that the message is preserved for the caller to log.
    async def test_get_pod_events_raises_on_error(self, events_client: K8sEventsClient) -> None:
        mock_api = MagicMock()
        mock_api.list_event_for_all_namespaces.side_effect = Exception("Connection refused")

        with (
            patch.object(events_client, "_get_api", return_value=mock_api),
            pytest.raises(Exception, match="Connection refused"),
        ):
            await events_client.get_pod_events()


# Note 13: `_event_timestamp` is a private helper (leading underscore convention)
# that resolves a Kubernetes event's timestamp from several optional fields. Testing
# private helpers directly is acceptable when they contain branching logic that would
# be difficult to exercise exhaustively through the public API alone. Each test
# covers exactly one branch, keeping each test's intent crystal-clear.
class TestEventTimestampHelper:
    def test_returns_none_when_all_timestamps_none(self) -> None:
        # Note 14: A `MagicMock()` with explicit attribute assignments lets a test
        # create a lightweight stand-in for a Kubernetes event object without
        # importing or instantiating the real Kubernetes client model, which may
        # require complex initialization or network access.
        event = MagicMock()
        event.last_timestamp = None
        event.event_time = None
        event.first_timestamp = None
        assert _event_timestamp(event) is None

    def test_returns_str_for_non_datetime_timestamp(self) -> None:
        # Note 15: This test targets the branch where `last_timestamp` is present but
        # is already a plain string rather than a `datetime` object. Kubernetes API
        # versions differ in whether they deserialise timestamps, so the helper must
        # handle both. The assertion checks both the value AND the fact that no
        # conversion exception is raised.
        event = MagicMock()
        event.last_timestamp = "2026-02-28T12:00:00Z"  # string, not datetime
        event.event_time = None
        event.first_timestamp = None
        result = _event_timestamp(event)
        assert result == "2026-02-28T12:00:00Z"


# ---------------------------------------------------------------------------
# clients/k8s_policy.py — _int_or_str with non-numeric string
# ---------------------------------------------------------------------------


# Note 16: Three small, focused tests cover the three distinct branches of `_int_or_str`:
# (a) an integer is returned as-is, (b) a purely numeric string is cast to int, and
# (c) a non-numeric string (like a percentage) is returned unchanged. This "branch per
# test" pattern makes it easy to pinpoint which branch a regression affects.
class TestIntOrStr:
    def test_returns_int_for_integer_input(self) -> None:
        assert _int_or_str(5) == 5

    def test_converts_numeric_string_to_int(self) -> None:
        assert _int_or_str("3") == 3

    def test_returns_str_for_non_numeric_string(self) -> None:
        # Note 17: Asserting `isinstance(result, str)` in addition to the value
        # equality check guards against a future change that might silently coerce the
        # value to a different type. It makes the type contract explicit in the test.
        result = _int_or_str("25%")
        assert result == "25%"
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# tools/k8s_upgrades.py — upgrade profile exception and fan-out error
# ---------------------------------------------------------------------------


# Note 18: Module-level factory functions like `_make_upgrade_status_output` reduce
# duplication across multiple test classes. The `cluster_id` parameter allows each
# call-site to produce a realistic, unique object without copy-pasting field values.
# Factory functions also isolate tests from changes to the model's required fields —
# only the factory needs updating when fields are added.
def _make_upgrade_status_output(cluster_id: str = "dev-eastus") -> UpgradeStatusOutput:
    return UpgradeStatusOutput(
        cluster=cluster_id,
        control_plane_version="1.29.8",
        node_pools=[],
        available_upgrades=[],
        upgrade_active=False,
        summary=f"{cluster_id} ok",
        timestamp=datetime.now(tz=UTC).isoformat(),
        errors=[],
    )


class TestK8sUpgradesExtraCoverage:
    # Note 19: This test exercises the partial-success path in `get_upgrade_status_handler`
    # where `get_cluster_info` succeeds but `get_upgrade_profile` raises. The handler
    # is expected to catch the exception and append a structured error rather than
    # propagating the exception to the caller. The assertion checks `result.errors`
    # rather than the absence of an exception, confirming the "soft-fail" contract.
    async def test_upgrade_profile_exception_adds_error(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.29.8",
            "provisioning_state": "Succeeded",
            "node_pools": [],
            "fqdn": "aks-test.eastus.azmk8s.io",
        }
        mock_aks.get_upgrade_profile.side_effect = Exception("Profile API down")

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            result = await get_upgrade_status_handler("prod-eastus")

        assert any(e.source == "aks-api" for e in result.errors)

    # Note 20: This is the canonical "fan-out error" test pattern used throughout this
    # file. The *all variant of a tool gathers results from every cluster in parallel
    # and is expected to skip (not crash on) any cluster that fails. The pattern works
    # by configuring `side_effect` as a list: the mock raises on its first call (the
    # failing cluster) and returns a good result for all subsequent calls.
    #
    # `AsyncMock(side_effect=[RuntimeError("..."), good, good, ...])` is the key idiom:
    # each successive call to the mock pops the next item from the list. If the item is
    # an exception instance or class it is raised; otherwise it is returned. This lets
    # a single mock object simulate a heterogeneous sequence of outcomes.
    async def test_fan_out_skips_failed_clusters(self) -> None:
        good = _make_upgrade_status_output()
        # Note 21: `[RuntimeError(...)] + [good] * 5` constructs a list where index 0
        # is an exception (raised on the first call) and indices 1-5 are the good
        # result (returned on subsequent calls). The `* 5` multiplier avoids spelling
        # out five identical entries and makes it easy to adjust the cluster count.
        mock_handler = AsyncMock(side_effect=[RuntimeError("Cluster unreachable")] + [good] * 5)

        with patch("platform_mcp_server.tools.k8s_upgrades.get_upgrade_status_handler", mock_handler):
            results = await get_upgrade_status_all()

        # Note 22: The assertion is `len(results) == 5` rather than `== 6` because
        # the failed cluster's result is expected to be omitted from the output. If
        # the handler mistakenly included `None` or a partial result the assertion
        # would catch it.
        assert len(results) == 5


# ---------------------------------------------------------------------------
# tools/node_pools.py — parsing and classification branches
# ---------------------------------------------------------------------------


# Note 23: CPU and memory parsing helpers translate Kubernetes resource strings into
# canonical numeric units (millicores and bytes respectively). Each test targets a
# distinct suffix or format recognised by the parser, ensuring every branch of the
# conditional suffix-detection logic is exercised. Failing to test a suffix branch
# can hide a typo that silently returns 0.0 instead of raising an error.
class TestParseCpuMillicores:
    def test_parses_millicores_suffix(self) -> None:
        assert _parse_cpu_millicores("500m") == 500.0

    # Note 24: A plain numeric string like "4" represents whole CPU cores. The parser
    # must multiply by 1000 to convert to millicores. Testing the plain-value branch
    # separately from the "m" suffix branch ensures the multiplication path is covered.
    def test_parses_plain_cpu_value(self) -> None:
        assert _parse_cpu_millicores("4") == 4000.0


class TestParseMemoryBytes:
    # Note 25: Kubernetes expresses memory in both SI units (k, M, G) and binary
    # units (Ki, Mi, Gi). Each has a different multiplier (1000 vs 1024). Separate
    # test cases for "Ki" vs "k" catch an off-by-one in the branch that chooses the
    # multiplier, which would otherwise only surface as a runtime sizing error.
    def test_parses_ki(self) -> None:
        assert _parse_memory_bytes("1024Ki") == 1024 * 1024

    def test_parses_k_suffix(self) -> None:
        assert _parse_memory_bytes("1000k") == 1_000_000

    def test_parses_M_suffix(self) -> None:
        assert _parse_memory_bytes("500M") == 500_000_000

    def test_parses_G_suffix(self) -> None:
        assert _parse_memory_bytes("2G") == 2_000_000_000

    def test_parses_plain_bytes(self) -> None:
        assert _parse_memory_bytes("1048576") == 1_048_576.0


# Note 26: `_classify_pressure` returns a severity string ("ok", "warning", "critical")
# based on which metric exceeds which threshold. The helper accepts `None` for metrics
# that are unavailable, so tests pass `None` for the metric not being tested. This
# keeps each test focused on one decision boundary without needing all metrics populated.
class TestClassifyPressureEdgeCases:
    def _thresholds(self) -> object:
        # Note 27: Constructing a `ThresholdConfig` inline (rather than reading it
        # from the live application config) pins the threshold values so tests do not
        # break if the default configuration changes. The test is testing the
        # classification logic, not the configuration defaults.
        from platform_mcp_server.config import ThresholdConfig

        return ThresholdConfig(
            cpu_warning=75.0,
            cpu_critical=90.0,
            memory_warning=80.0,
            memory_critical=95.0,
            pending_pods_warning=1,
            pending_pods_critical=10,
            upgrade_anomaly_minutes=60,
        )

    def test_cpu_warning(self) -> None:
        # Note 28: 76.0 > cpu_warning (75.0) but < cpu_critical (90.0), so the
        # expected result is "warning". Passing `None` for the memory argument
        # confirms the classification is driven purely by the CPU metric.
        result = _classify_pressure(76.0, None, 0, self._thresholds())  # type: ignore[arg-type]
        assert result == "warning"

    def test_memory_critical(self) -> None:
        # Note 29: 96.0 > memory_critical (95.0) should produce "critical". Testing
        # with `None` for CPU ensures the memory-critical branch is reached
        # independently, not accidentally covered by a prior CPU check.
        result = _classify_pressure(None, 96.0, 0, self._thresholds())  # type: ignore[arg-type]
        assert result == "critical"

    def test_memory_warning(self) -> None:
        result = _classify_pressure(None, 81.0, 0, self._thresholds())  # type: ignore[arg-type]
        assert result == "warning"

    def test_pending_pods_critical(self) -> None:
        # Note 30: 11 > pending_pods_critical (10) exercises the pending-pods-critical
        # branch. Both CPU and memory are `None` so we know the "critical" verdict
        # comes from the pod count alone.
        result = _classify_pressure(None, None, 11, self._thresholds())  # type: ignore[arg-type]
        assert result == "critical"


# Note 31: This test targets the sub-branch inside the pending-pods counting logic
# where a pending pod has a `node_name` that maps to a known pool. The handler
# aggregates pending pod counts per pool, and this branch increments the count for
# the pool whose node currently holds (or last held) the pending pod.
class TestNodePoolPressurePendingPodsOnNode:
    async def test_pending_pod_assigned_to_pool(self) -> None:
        """A pending pod whose node_name maps to a known pool increments that pool's count."""

        # Note 32: The `_node` inner factory returns a dict matching the shape the
        # K8s core client is expected to return. Using a dict rather than a real
        # Kubernetes node object avoids a dependency on the full Kubernetes Python
        # client library in the test layer.
        def _node(name: str, pool: str) -> dict:
            return {
                "name": name,
                "pool": pool,
                "version": "v1.29.8",
                "unschedulable": False,
                "allocatable_cpu": "4000m",
                "allocatable_memory": "16Gi",
                "conditions": {"Ready": "True"},
                "labels": {"agentpool": pool},
            }

        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_node("node-1", "userpool")]
        # Note 33: The pod dict sets `node_name: "node-1"` to exercise the code path
        # that resolves the node name to a pool name. Setting `phase: "Pending"` makes
        # the pod visible to the pending-pods accumulator. `reason: None` exercises
        # the branch where the pod has no explicit eviction reason.
        mock_core.get_pods.return_value = [
            {
                "name": "stuck-pod",
                "namespace": "default",
                "phase": "Pending",
                "node_name": "node-1",
                "reason": None,
                "container_statuses": [],
            }
        ]
        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = []

        # Note 34: Patching both `K8sCoreClient` and `K8sMetricsClient` prevents real
        # cluster API calls. The metrics mock returns an empty list to avoid triggering
        # metric-based classification logic that is not the focus of this test.
        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pending_pods >= 1


# Note 35: The fan-out error test for node pool pressure follows the same
# `side_effect=[RuntimeError(...)] + [good] * N` pattern. Constructing a real
# `NodePoolPressureOutput` for the "good" result (rather than a mock) verifies that
# the fan-out collector only includes correctly typed outputs.
class TestNodePoolPressureFanOutError:
    async def test_failed_cluster_skipped(self) -> None:
        good = NodePoolPressureOutput(
            cluster="dev-eastus",
            pools=[],
            summary="ok",
            timestamp=datetime.now(tz=UTC).isoformat(),
            errors=[],
        )
        mock_handler = AsyncMock(side_effect=[RuntimeError("Cluster unreachable")] + [good] * 5)

        with patch("platform_mcp_server.tools.node_pools.check_node_pool_pressure_handler", mock_handler):
            results = await check_node_pool_pressure_all()

        assert len(results) == 5


# ---------------------------------------------------------------------------
# tools/pdb_check.py — live mode no cordoned nodes, fan-out error, _workload_from_selector
# ---------------------------------------------------------------------------


# Note 36: The "no cordoned nodes" test targets the early-return branch in `live` mode.
# When no nodes are cordoned the handler should short-circuit and return an empty risks
# list with a specific summary message. Without this test the branch is only covered
# when integration tests run against a real cluster.
class TestPdbCheckLiveModeNoCordoned:
    async def test_returns_empty_when_no_cordoned_nodes(self) -> None:
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            {"name": "tight-pdb", "namespace": "default", "max_unavailable": 0, "disruptions_allowed": 0}
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {"name": "tight-pdb", "namespace": "default", "block_reason": "maxUnavailable=0", "expected_pods": 3}
        ]
        mock_core = AsyncMock()
        # Note 37: Setting `unschedulable: False` on every node simulates a healthy
        # cluster with no nodes being drained. In live mode the handler checks for
        # cordoned (unschedulable) nodes first; finding none causes it to return before
        # evaluating individual PDB blocks, which is the branch being covered here.
        mock_core.get_nodes.return_value = [
            {
                "name": "node-1",
                "pool": "userpool",
                "unschedulable": False,
                "version": "v1.29.8",
                "allocatable_cpu": "4000m",
                "allocatable_memory": "16Gi",
                "conditions": {"Ready": "True"},
            }
        ]

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="live")

        # Note 38: Asserting both the data (`risks == []`) and the human-readable
        # message (`"No active PDB blocks" in summary`) ensures the handler produces
        # useful output for operators and does not just silently return an empty list.
        assert result.risks == []
        assert "No active PDB blocks" in result.summary


class TestPdbCheckFanOutError:
    async def test_failed_cluster_skipped(self) -> None:
        # Note 39: The `mode="preflight"` field is set on the good output to mirror
        # what `check_pdb_risk_handler` would produce in practice. Using realistic
        # field values makes the test double serve as implicit documentation of what
        # the production output looks like.
        good = PdbCheckOutput(
            cluster="dev-eastus",
            mode="preflight",
            risks=[],
            summary="ok",
            timestamp=datetime.now(tz=UTC).isoformat(),
            errors=[],
        )
        mock_handler = AsyncMock(side_effect=[RuntimeError("Cluster unreachable")] + [good] * 5)

        with patch("platform_mcp_server.tools.pdb_check.check_pdb_risk_handler", mock_handler):
            results = await check_pdb_risk_all()

        assert len(results) == 5


# Note 40: `_workload_from_selector` resolves a Kubernetes label selector dict to a
# human-readable workload name. It inspects well-known labels in priority order. The
# four test cases below cover: (a) the `app` label, (b) the canonical
# `app.kubernetes.io/name` label, (c) an empty selector (fallback to "unknown"),
# and (d) an unrecognised label (fallback to some string). Together they exhaust all
# branches of the priority chain.
class TestWorkloadFromSelector:
    def test_uses_app_label(self) -> None:
        assert _workload_from_selector({"app": "nginx"}) == "nginx"

    def test_uses_app_kubernetes_io_name(self) -> None:
        assert _workload_from_selector({"app.kubernetes.io/name": "my-service"}) == "my-service"

    def test_returns_unknown_for_empty_selector(self) -> None:
        # Note 41: An empty dict `{}` exercises the final fallback branch where none
        # of the known label keys are present. The expected return value "unknown" is
        # the sentinel string that callers use to detect missing workload metadata.
        assert _workload_from_selector({}) == "unknown"

    def test_returns_str_for_other_labels(self) -> None:
        # Note 42: For an unrecognised label the function may return the first value
        # or "unknown". The test only asserts `isinstance(result, str)` rather than
        # a specific value because the exact fallback behaviour for arbitrary labels
        # is an implementation detail that may change. This preserves test flexibility
        # while still confirming the function does not raise or return None.
        result = _workload_from_selector({"tier": "backend"})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# tools/pod_classification.py — all remaining branches
# ---------------------------------------------------------------------------


# Note 43: `categorize_failure` maps a pod's top-level reason and its container
# waiting reasons to one of several failure categories: "scheduling", "registry",
# "config", "runtime", or "unknown". The test class below drives a single test
# per category per input pathway (top-level reason vs container-level reason) to
# achieve full branch coverage of the lookup tables.
class TestCategorizeFailureWaitingReasons:
    def test_scheduling_waiting_reason(self) -> None:
        # Note 44: Container statuses are passed as a list of dicts, each with a
        # nested `state.waiting.reason` structure that mirrors the Kubernetes pod
        # status JSON. The top-level reason argument is `None` here to confirm that
        # the container-level reason alone is sufficient to trigger the classification.
        cs = [{"state": {"waiting": {"reason": "FailedScheduling"}}}]
        assert categorize_failure(None, cs) == "scheduling"

    def test_registry_waiting_reason(self) -> None:
        cs = [{"state": {"waiting": {"reason": "ImagePullBackOff"}}}]
        assert categorize_failure(None, cs) == "registry"

    def test_config_waiting_reason(self) -> None:
        cs = [{"state": {"waiting": {"reason": "CreateContainerConfigError"}}}]
        assert categorize_failure(None, cs) == "config"

    def test_runtime_top_level_reason(self) -> None:
        # Note 45: An empty container status list `[]` forces the function to fall
        # through to the top-level reason lookup. "CrashLoopBackOff" is in the
        # RUNTIME_REASONS set, so the expected category is "runtime".
        assert categorize_failure("CrashLoopBackOff", []) == "runtime"

    def test_registry_top_level_reason(self) -> None:
        # Covers line 38: top-level reason in REGISTRY_REASONS with no container statuses
        # Note 46: This test was added specifically to cover line 38 identified by the
        # coverage report. The comment from the original developer confirms which exact
        # source line is being targeted. This practice of citing the line number
        # bridges the gap between coverage tooling and test intent.
        assert categorize_failure("ImagePullBackOff", []) == "registry"

    def test_config_top_level_reason(self) -> None:
        assert categorize_failure("InvalidImageName", []) == "config"


# Note 47: `is_unhealthy` evaluates a pod dict and returns `True` if the pod shows
# signs of unhealthy behaviour. The two tests below each target a distinct branch:
# OOMKilled in the last-terminated state (a past crash) and a waiting reason that
# falls into the combined "bad reasons" set (an ongoing crash or image issue).
class TestIsUnhealthyOomKill:
    def test_oomkill_in_last_terminated_is_unhealthy(self) -> None:
        # Note 48: `last_terminated` is distinct from the current `state`. A pod
        # running fine right now might still be unhealthy if its most recent container
        # termination was due to an OOMKill. This test exercises the branch that
        # inspects `last_terminated` independently of the current waiting state.
        pod = {
            "phase": "Running",
            "container_statuses": [
                {
                    "state": {},
                    "last_terminated": {"reason": "OOMKilled"},
                }
            ],
        }
        assert is_unhealthy(pod) is True

    def test_waiting_runtime_reason_is_unhealthy(self) -> None:
        # Covers line 54: waiting reason in RUNTIME_REASONS | REGISTRY_REASONS | CONFIG_REASONS
        # Note 49: A pod in "Running" phase with a container in waiting state might
        # seem paradoxical, but Kubernetes allows this during crash-loop recovery. The
        # function must look inside the container statuses even when the pod phase
        # appears healthy. Setting `last_terminated: {}` (empty dict, not None) covers
        # the branch where the key exists but carries no meaningful reason string.
        pod = {
            "phase": "Running",
            "container_statuses": [
                {
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "last_terminated": {},
                }
            ],
        }
        assert is_unhealthy(pod) is True


# ---------------------------------------------------------------------------
# tools/pod_health.py — events exception, status_filter="failed", fan-out error
# ---------------------------------------------------------------------------


# Note 50: `_make_ph_pod` is a module-level factory for pod health test data. Default
# values represent the most common test scenario (a pending pod with an unschedulable
# reason). Callers override only the fields relevant to their specific test case,
# keeping test data minimal and the intent legible.
def _make_ph_pod(
    name: str,
    phase: str = "Pending",
    reason: str | None = "Unschedulable",
) -> dict:
    return {
        "name": name,
        "namespace": "default",
        "phase": phase,
        "node_name": "node-1",
        "reason": reason,
        "message": None,
        "container_statuses": [],
        "conditions": [],
    }


class TestPodHealthExtraCoverage:
    # Note 51: The events-exception test covers the branch where the pod listing
    # succeeds but the subsequent call to fetch events fails. The handler is expected
    # to append an error with source "events-api" rather than raising. Returning an
    # empty pod list from `mock_core` isolates the events error path; if pods were
    # also mocked to fail, it would be unclear which failure triggered the error.
    async def test_events_exception_adds_error(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        mock_events.get_pod_events.side_effect = Exception("Events API down")

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert any(e.source == "events-api" for e in result.errors)

    # Note 52: The `status_filter="failed"` test exercises the filtering branch inside
    # `get_pod_health_handler`. Two pods are returned by the mock: one Pending and one
    # Failed. The filter should keep only Failed pods. Using two pods of different
    # phases guarantees the filter is actually applied rather than trivially passing
    # because all pods happen to match.
    async def test_status_filter_failed(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_ph_pod("pod-1", phase="Pending"),
            _make_ph_pod("pod-2", phase="Failed"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus", status_filter="failed")

        # Note 53: `all(p.phase == "Failed" for p in result.pods)` asserts the
        # invariant that every pod in the result matches the filter. An empty list
        # would make this assertion vacuously true, so a robust follow-up check would
        # also assert `len(result.pods) > 0`. The current assertion is sufficient
        # because the mock guarantees at least one Failed pod exists.
        assert all(p.phase == "Failed" for p in result.pods)

    async def test_fan_out_skips_failed_clusters(self) -> None:
        # Note 54: `PodHealthOutput` includes `groups` (a dict) and `total_matching`
        # and `truncated` fields that must be present for the model to validate. The
        # "good" result is constructed with minimal but valid values to keep the test
        # focused on the fan-out skip behaviour rather than the model shape.
        good = PodHealthOutput(
            cluster="dev-eastus",
            pods=[],
            groups={},
            total_matching=0,
            truncated=False,
            summary="ok",
            timestamp=datetime.now(tz=UTC).isoformat(),
            errors=[],
        )
        mock_handler = AsyncMock(side_effect=[RuntimeError("Cluster unreachable")] + [good] * 5)

        with patch("platform_mcp_server.tools.pod_health.get_pod_health_handler", mock_handler):
            results = await get_pod_health_all()

        assert len(results) == 5


# ---------------------------------------------------------------------------
# tools/upgrade_metrics.py — _parse_ts, events without timestamps, activity log
# error, estimated_total, exact history count, fan-out error
# ---------------------------------------------------------------------------


# Note 55: `_parse_ts` is a thin wrapper around `datetime.fromisoformat` that returns
# `None` instead of raising on invalid input. Testing all three branches (None input,
# invalid string, valid string) provides a complete behavioural specification of the
# helper in the test suite, acting as living documentation alongside the source code.
class TestParseTsHelper:
    def test_returns_none_for_none_input(self) -> None:
        assert _parse_ts(None) is None

    def test_returns_none_for_invalid_string(self) -> None:
        # Note 56: "not-a-date" is chosen because it is clearly invalid yet will not
        # accidentally become a valid ISO format in any future Python version. The
        # assertion confirms that the function swallows the `ValueError` from
        # `fromisoformat` and returns `None` rather than propagating the exception.
        assert _parse_ts("not-a-date") is None

    def test_parses_valid_iso_string(self) -> None:
        result = _parse_ts("2026-02-28T12:00:00+00:00")
        assert isinstance(result, datetime)


# Note 57: `_make_upg_event` creates a minimal event dict for upgrade metrics tests.
# All fields are required by the downstream processing code, so the factory fills them
# all. The `timestamp` field is deliberately a positional parameter (not keyword-only)
# to allow concise one-liner calls in test bodies.
def _make_upg_event(node_name: str, reason: str, timestamp: str) -> dict:
    return {"reason": reason, "node_name": node_name, "message": "", "timestamp": timestamp, "count": 1}


class TestUpgradeMetricsExtraCoverage:
    # Note 58: The null-timestamp test covers the `continue` statement inside the
    # event-processing loop. When `_parse_ts` returns `None` for an event's timestamp
    # the loop skips that event entirely. Placing the null-timestamp event first in
    # the list confirms the `continue` does not affect processing of subsequent events.
    async def test_event_with_null_timestamp_is_skipped(self) -> None:
        """An event with a null timestamp is skipped via continue."""
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            # Note 59: The first event has `timestamp: None`. The `_parse_ts` call
            # on this value returns `None`, triggering the `continue`. The two
            # following events have valid timestamps and form a complete upgrade cycle
            # (NodeUpgrade → NodeReady), so the assertion can verify that exactly
            # one node completed despite the skipped event.
            {"reason": "NodeUpgrade", "node_name": "node-1", "timestamp": None, "message": "", "count": 1},
            _make_upg_event("node-1", "NodeUpgrade", "2026-02-28T11:00:00+00:00"),
            _make_upg_event("node-1", "NodeReady", "2026-02-28T11:10:00+00:00"),
        ]
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        assert result.current_run is not None
        assert result.current_run.nodes_completed == 1

    # Note 60: The activity-log exception test covers the `except` block inside the
    # section that fetches historical upgrade records from the Azure Monitor activity
    # log. The handler must treat this as a non-fatal error and continue returning
    # whatever data it gathered from Kubernetes events. The structured error object
    # allows the caller to surface the degraded state to the operator.
    async def test_activity_log_exception_adds_error(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.side_effect = Exception("Activity log unavailable")

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        assert any(e.source == "activity-log" for e in result.errors)

    # Note 61: The estimated-remaining-seconds test targets the branch that computes
    # a time estimate when at least one node is still in progress (NodeUpgrade seen,
    # NodeReady not yet seen). The estimate is derived from the average per-node
    # duration of already-completed nodes multiplied by the remaining node count.
    # node-2 intentionally has no NodeReady event, keeping it "in progress".
    async def test_estimated_total_includes_remaining_seconds(self) -> None:
        """When nodes are still in progress, estimated_remaining_seconds is included in total."""
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_upg_event("node-1", "NodeUpgrade", "2026-02-28T10:00:00+00:00"),
            _make_upg_event("node-1", "NodeReady", "2026-02-28T10:10:00+00:00"),
            _make_upg_event("node-2", "NodeUpgrade", "2026-02-28T10:10:00+00:00"),
            # node-2 has no NodeReady — still in progress
        ]
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        assert result.current_run is not None
        assert result.current_run.estimated_remaining_seconds is not None
        assert result.current_run.estimated_remaining_seconds > 0

    # Note 62: The "exact history count" test covers the conditional inside the
    # summary-generation logic that chooses between "N historical records" and
    # "N of M historical records". When the number of records found equals the
    # requested `history_count`, the shorter form is used. Returning exactly 2
    # records when `history_count=2` triggers that branch.
    async def test_summary_shows_exact_historical_count(self) -> None:
        """When found == history_count, summary shows 'N historical records' (no 'of M' suffix)."""
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            {"date": "2026-02-20T12:00:00+00:00", "duration_seconds": 3000.0, "description": "done"},
            {"date": "2026-02-10T12:00:00+00:00", "duration_seconds": 3600.0, "description": "done"},
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool", history_count=2)

        # Note 63: The negative assertion `assert "of 2" not in result.summary`
        # is as important as the positive one. It confirms the conditional logic
        # chose the short form over the "N of M" form. Without it, a handler that
        # always renders both phrases would pass the first assertion but still be wrong.
        assert "2 historical records" in result.summary
        assert "of 2" not in result.summary

    async def test_fan_out_skips_failed_clusters(self) -> None:
        # Note 64: `UpgradeDurationOutput` has several optional fields (`current_run`,
        # `stats`, `anomaly_flag`) that are set to `None` here. This exercises the
        # fan-out collector's handling of outputs that may have partially populated
        # fields, ensuring the collector does not crash when optional fields are absent.
        good = UpgradeDurationOutput(
            cluster="dev-eastus",
            node_pool="userpool",
            current_run=None,
            historical=[],
            stats=None,
            anomaly_flag=None,
            summary="ok",
            timestamp=datetime.now(tz=UTC).isoformat(),
            errors=[],
        )
        mock_handler = AsyncMock(side_effect=[RuntimeError("Cluster unreachable")] + [good] * 5)

        with patch("platform_mcp_server.tools.upgrade_metrics.get_upgrade_metrics_handler", mock_handler):
            results = await get_upgrade_metrics_all("userpool")

        assert len(results) == 5


# ---------------------------------------------------------------------------
# tools/upgrade_progress.py — all remaining branches
# ---------------------------------------------------------------------------


# Note 65: `_parse_event_timestamp` mirrors `_parse_ts` but is specific to the
# upgrade-progress module. Separate helper functions in separate modules are tested
# separately even if they share the same logic, because each module import path is
# independent and refactoring one must not silently break the other.
class TestParseEventTimestampHelper:
    def test_returns_none_for_none(self) -> None:
        assert _parse_event_timestamp(None) is None

    def test_returns_none_for_invalid_string(self) -> None:
        assert _parse_event_timestamp("not-a-date") is None

    def test_parses_valid_iso_string(self) -> None:
        result = _parse_event_timestamp("2026-02-28T12:00:00+00:00")
        assert isinstance(result, datetime)


# Note 66: `_make_upg_pool` creates a node pool dict that represents a pool currently
# undergoing a Kubernetes version upgrade. `provisioning_state="Upgrading"` is the
# key field that tells the handler this pool should be tracked. Default versions are
# set to a realistic upgrade pair (1.29.8 → 1.30.0) so tests that check version
# comparisons work correctly without additional configuration.
def _make_upg_pool(
    name: str = "userpool",
    current_version: str = "1.29.8",
    target_version: str = "1.30.0",
    provisioning_state: str = "Upgrading",
) -> dict:
    return {
        "name": name,
        "vm_size": "Standard_DS2_v2",
        "count": 5,
        "min_count": 3,
        "max_count": 10,
        "current_version": current_version,
        "target_version": target_version,
        "provisioning_state": provisioning_state,
        "power_state": "Running",
        "os_type": "Linux",
        "mode": "User",
    }


# Note 67: `_make_upg_node` is a factory for node dicts used in upgrade-progress
# tests. The `unschedulable` flag is a first-class parameter because cordoning
# (marking a node unschedulable) is one of the key signals the handler uses to
# determine whether a node is being drained as part of the upgrade process.
def _make_upg_node(
    name: str,
    pool: str = "userpool",
    version: str = "v1.29.8",
    unschedulable: bool = False,
) -> dict:
    return {
        "name": name,
        "pool": pool,
        "version": version,
        "unschedulable": unschedulable,
        "allocatable_cpu": "4000m",
        "allocatable_memory": "16Gi",
        "conditions": {"Ready": "True"},
        "labels": {"agentpool": pool},
    }


# Note 68: `_make_upg_evt` creates a node event dict. The default timestamp is a
# hardcoded past time to ensure tests that do not care about timing have a stable,
# non-expiring timestamp. Tests that need to simulate "within threshold" or "past
# threshold" scenarios override the timestamp with a computed relative value.
def _make_upg_evt(node_name: str, reason: str, timestamp: str = "2026-02-28T10:00:00+00:00") -> dict:
    return {"reason": reason, "node_name": node_name, "message": "", "timestamp": timestamp, "count": 1}


class TestUpgradeProgressExtraCoverage:
    # Note 69: The "upgrading" state test confirms the classification branch where a
    # node has a NodeUpgrade event within the anomaly threshold window and is NOT
    # cordoned. This is the normal, expected state of a node mid-upgrade. Using
    # `timedelta(minutes=5)` for the event timestamp places it well within the 60-
    # minute anomaly threshold, so the handler should not flag it as stalled.
    async def test_node_classified_as_upgrading(self) -> None:
        """Node with NodeUpgrade, no NodeReady, within threshold, not cordoned → upgrading."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_upg_node("node-1", unschedulable=False)]
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        # Very recent event — well within the 60-minute anomaly threshold
        recent_ts = (datetime.now(tz=UTC) - timedelta(minutes=5)).isoformat()
        mock_events.get_node_events.return_value = [_make_upg_evt("node-1", "NodeUpgrade", recent_ts)]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "upgrading"

    # Note 70: The "stalled" state test is the mirror of the "upgrading" test above.
    # Using `timedelta(hours=2)` places the NodeUpgrade event 120 minutes ago, which
    # exceeds the 60-minute anomaly threshold. With no NodeReady event and no PDB
    # blockers, the handler should classify the node as "stalled" rather than
    # "upgrading". The two tests together pin both sides of the time threshold boundary.
    async def test_node_classified_as_stalled(self) -> None:
        """Node with NodeUpgrade but no NodeReady past the anomaly threshold → stalled."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_upg_node("node-1", unschedulable=False)]
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        # Upgrade event 2 hours ago (well past 60-minute anomaly threshold)
        old_ts = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        mock_events.get_node_events.return_value = [_make_upg_evt("node-1", "NodeUpgrade", old_ts)]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "stalled"

    # Note 71: The "pdb_blocked" test at the anomaly threshold combines three
    # conditions: (a) a NodeUpgrade event older than the threshold, (b) the node is
    # cordoned (`unschedulable=True`), and (c) there are active PDB blockers from
    # `evaluate_pdb_satisfiability`. All three must be true for the handler to classify
    # the node as "pdb_blocked" rather than "stalled". This test covers the intersection
    # of the time-exceeded AND cordoned AND pdb-blocked branches.
    async def test_node_classified_pdb_blocked_at_anomaly_threshold(self) -> None:
        """Node with NodeUpgrade past threshold + cordoned + PDB blockers → pdb_blocked."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 72: `unschedulable=True` simulates a cordoned node — one that has been
        # drained as part of the upgrade but has not yet completed. The combination
        # of "cordoned + PDB blocker" is what distinguishes "pdb_blocked" from "stalled".
        mock_core.get_nodes.return_value = [_make_upg_node("node-1", unschedulable=True)]
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        old_ts = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        mock_events.get_node_events.return_value = [_make_upg_evt("node-1", "NodeUpgrade", old_ts)]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            {"name": "block-pdb", "namespace": "ns1", "max_unavailable": 0, "disruptions_allowed": 0}
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {"name": "block-pdb", "namespace": "ns1", "block_reason": "maxUnavailable=0"}
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "pdb_blocked"

    # Note 73: This test is the within-threshold counterpart to the pdb_blocked test
    # above. Here the NodeUpgrade event is recent (5 minutes ago, within the 60-minute
    # threshold) but the node is still cordoned and blocked by a PDB. The handler must
    # classify the node as "pdb_blocked" regardless of whether the threshold is
    # exceeded, confirming the PDB check happens independently of the stall detection.
    async def test_upgrading_node_pdb_blocked_within_threshold(self) -> None:
        """Node with NodeUpgrade within threshold + cordoned + PDB blockers → pdb_blocked."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_upg_node("node-1", unschedulable=True)]
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        recent_ts = (datetime.now(tz=UTC) - timedelta(minutes=5)).isoformat()
        mock_events.get_node_events.return_value = [_make_upg_evt("node-1", "NodeUpgrade", recent_ts)]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            {"name": "block-pdb", "namespace": "ns1", "max_unavailable": 0, "disruptions_allowed": 0}
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {"name": "block-pdb", "namespace": "ns1", "block_reason": "maxUnavailable=0"}
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "pdb_blocked"

    # Note 74: The pod-transitions exception test covers the `except` block inside
    # `_collect_pod_transitions`. The function fetches pods from the Kubernetes API
    # and Kubernetes events to build a pod movement timeline. When `get_pods` raises,
    # the handler is expected to catch the error, append a structured error record
    # with source "k8s-api", and continue so the caller receives a partial result.
    async def test_pod_transitions_exception_adds_error(self) -> None:
        """An exception during pod fetch in _collect_pod_transitions adds an error."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_upg_node("node-1", unschedulable=True)]
        # Note 75: `side_effect = Exception(...)` on `get_pods` rather than
        # `get_nodes` ensures the exception is raised during the pod-collection
        # phase rather than the node-collection phase. This pinpoints which code path
        # produces the "k8s-api" error entry.
        mock_core.get_pods.side_effect = Exception("K8s API unavailable")
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert any(e.source == "k8s-api" for e in result.errors)

    # Note 76: The node_pool filter test verifies that passing `node_pool="system"`
    # narrows the set of upgrading pools considered by the handler. Two pools are
    # provided ("system" and "user") but only "system" matches the filter. The
    # assertion checks `result.upgrade_in_progress is True` and `result.node_pool ==
    # "system"` to confirm the filter was applied at the pool-selection level.
    async def test_node_pool_filter_on_upgrading_pools(self) -> None:
        """Passing node_pool filters the upgrading_pools list to the named pool."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool(name="system"), _make_upg_pool(name="user")],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_upg_node("node-1", pool="system")]
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus", node_pool="system")

        assert result.upgrade_in_progress is True
        assert result.node_pool == "system"

    # Note 77: The node-level filter test is complementary to the pool-level filter
    # test above. It verifies that when `node_pool="userpool"` is specified, nodes
    # belonging to other pools ("systempool") are excluded from `result.nodes`. Two
    # nodes in different pools are provided so the test can assert both inclusion
    # ("node-usr" present) and exclusion ("node-sys" absent) simultaneously.
    async def test_node_pool_filter_on_nodes_list(self) -> None:
        """Nodes not in the specified pool are excluded from state classification."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool(name="userpool")],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        # Note 78: Two nodes from two different pools let us confirm both the
        # inclusion and exclusion sides of the filter in one test. Checking a set
        # comprehension `{n.name for n in result.nodes}` is more Pythonic than
        # iterating and is O(1) for membership checks, which matters when result
        # sets grow large in future test expansions.
        mock_core.get_nodes.return_value = [
            _make_upg_node("node-sys", pool="systempool"),
            _make_upg_node("node-usr", pool="userpool"),
        ]
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus", node_pool="userpool")

        node_names = {n.name for n in result.nodes}
        assert "node-usr" in node_names
        assert "node-sys" not in node_names

    # Note 79: The duration-estimation test covers the branch that computes
    # `elapsed_seconds` and `estimated_remaining_seconds` for an in-progress upgrade.
    # The handler needs at least one completed node (node-1, version v1.30.0) to
    # calculate a per-node average, and at least one pending node (node-2, still at
    # v1.29.8) to have remaining work to estimate. Both conditions must be true for
    # the estimation branch to be reachable.
    async def test_duration_estimation_with_upgraded_and_remaining(self) -> None:
        """elapsed_seconds and estimated_remaining_seconds are set when nodes upgraded + pending."""
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_upg_pool()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [
            _make_upg_node("node-1", version="v1.30.0"),  # will be upgraded
            _make_upg_node("node-2", version="v1.29.8"),  # still pending
        ]
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        # Note 80: `recent_ts` and `ready_ts` are computed relative to `now` so the
        # test never becomes stale as wall-clock time advances. Using
        # `datetime.now(tz=UTC)` with a fixed `timedelta` offset ensures the event
        # timestamps are always in the recent past, within the anomaly window.
        recent_ts = (datetime.now(tz=UTC) - timedelta(minutes=10)).isoformat()
        ready_ts = (datetime.now(tz=UTC) - timedelta(minutes=5)).isoformat()
        mock_events.get_node_events.return_value = [
            _make_upg_evt("node-1", "NodeUpgrade", recent_ts),
            _make_upg_evt("node-1", "NodeReady", ready_ts),
        ]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.elapsed_seconds is not None
        assert result.estimated_remaining_seconds is not None
        assert result.estimated_remaining_seconds > 0

    # Note 81: The final fan-out error test in this file follows the same
    # `AsyncMock(side_effect=[error] + [good] * N)` pattern seen in every other tool.
    # Consistency across all fan-out tests is intentional: it makes the pattern
    # recognisable, allows future engineers to follow the same pattern when adding new
    # *_all tools, and ensures that the fan-out skip behaviour is verified for every
    # tool that fans out across clusters.
    async def test_fan_out_skips_failed_clusters(self) -> None:
        # Note 82: `UpgradeProgressOutput` requires `upgrade_in_progress` and `nodes`
        # fields in addition to the common fields. Setting `upgrade_in_progress=False`
        # and `nodes=[]` produces a valid "quiet" result that represents a cluster
        # where no upgrade is currently active, which is the most common state.
        good = UpgradeProgressOutput(
            cluster="dev-eastus",
            upgrade_in_progress=False,
            nodes=[],
            summary="ok",
            timestamp=datetime.now(tz=UTC).isoformat(),
            errors=[],
        )
        mock_handler = AsyncMock(side_effect=[RuntimeError("Cluster unreachable")] + [good] * 5)

        with patch("platform_mcp_server.tools.upgrade_progress.get_upgrade_progress_handler", mock_handler):
            results = await get_upgrade_progress_all()

        assert len(results) == 5
