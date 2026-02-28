"""Tests for get_pod_health tool handler."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.pod_health import get_pod_health_handler


def _make_pod(
    name: str,
    namespace: str = "default",
    phase: str = "Running",
    node_name: str = "node-1",
    reason: str | None = None,
    container_statuses: list | None = None,
) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "node_name": node_name,
        "reason": reason,
        "message": None,
        "container_statuses": container_statuses or [],
        "conditions": [],
    }


def _make_event(
    pod_name: str,
    namespace: str = "default",
    reason: str = "FailedScheduling",
    message: str = "0/12 nodes available",
    timestamp: str | None = None,
) -> dict:
    ts = timestamp or datetime.now(tz=UTC).isoformat()
    return {
        "reason": reason,
        "pod_name": pod_name,
        "namespace": namespace,
        "message": message,
        "timestamp": ts,
        "count": 1,
    }


class TestGetPodHealth:
    async def test_happy_path_pending_pods(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending", reason="Unschedulable"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = [
            _make_event("pod-1", reason="FailedScheduling", message="Insufficient cpu"),
        ]

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert len(result.pods) == 1
        assert result.pods[0].phase == "Pending"
        assert result.pods[0].failure_category == "scheduling"

    async def test_failure_reason_grouping(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending", reason="Unschedulable"),
            _make_pod("pod-2", phase="Pending", reason="Unschedulable"),
            _make_pod(
                "pod-3",
                phase="Failed",
                container_statuses=[
                    {
                        "name": "app",
                        "ready": False,
                        "restart_count": 5,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    }
                ],
            ),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert result.groups.get("scheduling", 0) == 2
        assert result.groups.get("runtime", 0) == 1

    async def test_oomkill_detection(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod(
                "pod-1",
                phase="Running",
                container_statuses=[
                    {
                        "name": "worker",
                        "ready": False,
                        "restart_count": 3,
                        "state": {},
                        "last_terminated": {"reason": "OOMKilled", "exit_code": 137},
                    }
                ],
            ),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert len(result.pods) == 1
        assert result.pods[0].failure_category == "runtime"
        assert result.pods[0].container_name == "worker"

    async def test_result_cap_at_50(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod(f"pod-{i}", phase="Pending", reason="Unschedulable") for i in range(120)
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert len(result.pods) == 50
        assert result.total_matching == 120
        assert result.truncated is True

    async def test_namespace_filtering(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", namespace="payments", phase="Pending"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus", namespace="payments")

        assert len(result.pods) == 1
        mock_core.get_pods.assert_called_once_with(namespace="payments")

    async def test_status_filter_pending(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending"),
            _make_pod("pod-2", phase="Failed"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus", status_filter="pending")

        assert all(p.phase == "Pending" for p in result.pods)

    async def test_event_context_per_pod(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending"),
        ]
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = [
            _make_event("pod-1", message="0/12 nodes available: Insufficient cpu"),
        ]

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            result = await get_pod_health_handler("prod-eastus")

        assert result.pods[0].last_event == "0/12 nodes available: Insufficient cpu"

    async def test_cluster_all_fan_out(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_pods.return_value = []
        mock_events = AsyncMock()
        mock_events.get_pod_events.return_value = []

        with (
            patch("platform_mcp_server.tools.pod_health.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.pod_health.K8sEventsClient", return_value=mock_events),
        ):
            from platform_mcp_server.tools.pod_health import get_pod_health_all

            results = await get_pod_health_all()

        assert len(results) == 6
