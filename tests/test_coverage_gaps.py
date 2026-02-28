"""Tests targeting previously uncovered lines across all modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class TestLoadK8sApiClient:
    def test_calls_new_client_from_config(self) -> None:
        from platform_mcp_server.clients import load_k8s_api_client

        with patch("platform_mcp_server.clients.new_client_from_config") as mock_fn:
            mock_fn.return_value = MagicMock()
            result = load_k8s_api_client("aks-prod-eastus")

        mock_fn.assert_called_once_with(context="aks-prod-eastus")
        assert result is mock_fn.return_value


# ---------------------------------------------------------------------------
# clients/azure_aks.py — exception paths and break in activity log
# ---------------------------------------------------------------------------


@pytest.fixture
def aks_client() -> AzureAksClient:
    return AzureAksClient(CLUSTER_MAP["prod-eastus"])


class TestAzureAksClientErrorPaths:
    async def test_get_node_pool_state_raises_on_error(self, aks_client: AzureAksClient) -> None:
        mock_container = MagicMock()
        mock_container.agent_pools.get.side_effect = Exception("Forbidden")

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

        def _make_entry() -> MagicMock:
            e = MagicMock()
            e.status.value = "Succeeded"
            e.event_timestamp = now
            e.submission_timestamp = now - timedelta(hours=1)
            e.operation_name.value = "Microsoft.ContainerService/managedClusters/write"
            e.description = "Upgrade completed"
            return e

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
    async def test_get_pod_events_raises_on_error(self, events_client: K8sEventsClient) -> None:
        mock_api = MagicMock()
        mock_api.list_event_for_all_namespaces.side_effect = Exception("Connection refused")

        with (
            patch.object(events_client, "_get_api", return_value=mock_api),
            pytest.raises(Exception, match="Connection refused"),
        ):
            await events_client.get_pod_events()


class TestEventTimestampHelper:
    def test_returns_none_when_all_timestamps_none(self) -> None:
        event = MagicMock()
        event.last_timestamp = None
        event.event_time = None
        event.first_timestamp = None
        assert _event_timestamp(event) is None

    def test_returns_str_for_non_datetime_timestamp(self) -> None:
        event = MagicMock()
        event.last_timestamp = "2026-02-28T12:00:00Z"  # string, not datetime
        event.event_time = None
        event.first_timestamp = None
        result = _event_timestamp(event)
        assert result == "2026-02-28T12:00:00Z"


# ---------------------------------------------------------------------------
# clients/k8s_policy.py — _int_or_str with non-numeric string
# ---------------------------------------------------------------------------


class TestIntOrStr:
    def test_returns_int_for_integer_input(self) -> None:
        assert _int_or_str(5) == 5

    def test_converts_numeric_string_to_int(self) -> None:
        assert _int_or_str("3") == 3

    def test_returns_str_for_non_numeric_string(self) -> None:
        result = _int_or_str("25%")
        assert result == "25%"
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# tools/k8s_upgrades.py — upgrade profile exception and fan-out error
# ---------------------------------------------------------------------------


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

    async def test_fan_out_skips_failed_clusters(self) -> None:
        good = _make_upgrade_status_output()
        mock_handler = AsyncMock(side_effect=[RuntimeError("Cluster unreachable")] + [good] * 5)

        with patch("platform_mcp_server.tools.k8s_upgrades.get_upgrade_status_handler", mock_handler):
            results = await get_upgrade_status_all()

        assert len(results) == 5


# ---------------------------------------------------------------------------
# tools/node_pools.py — parsing and classification branches
# ---------------------------------------------------------------------------


class TestParseCpuMillicores:
    def test_parses_millicores_suffix(self) -> None:
        assert _parse_cpu_millicores("500m") == 500.0

    def test_parses_plain_cpu_value(self) -> None:
        assert _parse_cpu_millicores("4") == 4000.0


class TestParseMemoryBytes:
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


class TestClassifyPressureEdgeCases:
    def _thresholds(self) -> object:
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
        result = _classify_pressure(76.0, None, 0, self._thresholds())  # type: ignore[arg-type]
        assert result == "warning"

    def test_memory_critical(self) -> None:
        result = _classify_pressure(None, 96.0, 0, self._thresholds())  # type: ignore[arg-type]
        assert result == "critical"

    def test_memory_warning(self) -> None:
        result = _classify_pressure(None, 81.0, 0, self._thresholds())  # type: ignore[arg-type]
        assert result == "warning"

    def test_pending_pods_critical(self) -> None:
        result = _classify_pressure(None, None, 11, self._thresholds())  # type: ignore[arg-type]
        assert result == "critical"


class TestNodePoolPressurePendingPodsOnNode:
    async def test_pending_pod_assigned_to_pool(self) -> None:
        """A pending pod whose node_name maps to a known pool increments that pool's count."""

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

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pending_pods >= 1


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

        assert result.risks == []
        assert "No active PDB blocks" in result.summary


class TestPdbCheckFanOutError:
    async def test_failed_cluster_skipped(self) -> None:
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


class TestWorkloadFromSelector:
    def test_uses_app_label(self) -> None:
        assert _workload_from_selector({"app": "nginx"}) == "nginx"

    def test_uses_app_kubernetes_io_name(self) -> None:
        assert _workload_from_selector({"app.kubernetes.io/name": "my-service"}) == "my-service"

    def test_returns_unknown_for_empty_selector(self) -> None:
        assert _workload_from_selector({}) == "unknown"

    def test_returns_str_for_other_labels(self) -> None:
        result = _workload_from_selector({"tier": "backend"})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# tools/pod_classification.py — all remaining branches
# ---------------------------------------------------------------------------


class TestCategorizeFailureWaitingReasons:
    def test_scheduling_waiting_reason(self) -> None:
        cs = [{"state": {"waiting": {"reason": "FailedScheduling"}}}]
        assert categorize_failure(None, cs) == "scheduling"

    def test_registry_waiting_reason(self) -> None:
        cs = [{"state": {"waiting": {"reason": "ImagePullBackOff"}}}]
        assert categorize_failure(None, cs) == "registry"

    def test_config_waiting_reason(self) -> None:
        cs = [{"state": {"waiting": {"reason": "CreateContainerConfigError"}}}]
        assert categorize_failure(None, cs) == "config"

    def test_runtime_top_level_reason(self) -> None:
        assert categorize_failure("CrashLoopBackOff", []) == "runtime"

    def test_registry_top_level_reason(self) -> None:
        # Covers line 38: top-level reason in REGISTRY_REASONS with no container statuses
        assert categorize_failure("ImagePullBackOff", []) == "registry"

    def test_config_top_level_reason(self) -> None:
        assert categorize_failure("InvalidImageName", []) == "config"


class TestIsUnhealthyOomKill:
    def test_oomkill_in_last_terminated_is_unhealthy(self) -> None:
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

        assert all(p.phase == "Failed" for p in result.pods)

    async def test_fan_out_skips_failed_clusters(self) -> None:
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


class TestParseTsHelper:
    def test_returns_none_for_none_input(self) -> None:
        assert _parse_ts(None) is None

    def test_returns_none_for_invalid_string(self) -> None:
        assert _parse_ts("not-a-date") is None

    def test_parses_valid_iso_string(self) -> None:
        result = _parse_ts("2026-02-28T12:00:00+00:00")
        assert isinstance(result, datetime)


def _make_upg_event(node_name: str, reason: str, timestamp: str) -> dict:
    return {"reason": reason, "node_name": node_name, "message": "", "timestamp": timestamp, "count": 1}


class TestUpgradeMetricsExtraCoverage:
    async def test_event_with_null_timestamp_is_skipped(self) -> None:
        """An event with a null timestamp is skipped via continue."""
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
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

        assert "2 historical records" in result.summary
        assert "of 2" not in result.summary

    async def test_fan_out_skips_failed_clusters(self) -> None:
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


class TestParseEventTimestampHelper:
    def test_returns_none_for_none(self) -> None:
        assert _parse_event_timestamp(None) is None

    def test_returns_none_for_invalid_string(self) -> None:
        assert _parse_event_timestamp("not-a-date") is None

    def test_parses_valid_iso_string(self) -> None:
        result = _parse_event_timestamp("2026-02-28T12:00:00+00:00")
        assert isinstance(result, datetime)


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


def _make_upg_evt(node_name: str, reason: str, timestamp: str = "2026-02-28T10:00:00+00:00") -> dict:
    return {"reason": reason, "node_name": node_name, "message": "", "timestamp": timestamp, "count": 1}


class TestUpgradeProgressExtraCoverage:
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

    async def test_fan_out_skips_failed_clusters(self) -> None:
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
