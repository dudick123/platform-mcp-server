"""Tests for get_upgrade_duration_metrics tool handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.upgrade_metrics import get_upgrade_metrics_handler


def _make_node_event(node_name: str, reason: str, timestamp: str) -> dict:
    return {
        "reason": reason,
        "node_name": node_name,
        "message": f"{reason} on {node_name}",
        "timestamp": timestamp,
        "count": 1,
    }


def _make_activity_record(
    date: str = "2026-02-20T12:00:00+00:00",
    duration_seconds: float = 3000.0,
) -> dict:
    return {
        "date": date,
        "operation": "Microsoft.ContainerService/managedClusters/write",
        "status": "Succeeded",
        "duration_seconds": duration_seconds,
        "description": "Upgrade completed",
    }


class TestGetUpgradeMetrics:
    async def test_current_run_timing(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_node_event("node-1", "NodeUpgrade", "2026-02-28T11:00:00+00:00"),
            _make_node_event("node-1", "NodeReady", "2026-02-28T11:05:00+00:00"),
            _make_node_event("node-2", "NodeUpgrade", "2026-02-28T11:05:00+00:00"),
            _make_node_event("node-2", "NodeReady", "2026-02-28T11:08:00+00:00"),
        ]
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        assert result.current_run is not None
        assert result.current_run.nodes_completed == 2
        assert result.current_run.mean_seconds_per_node > 0

    async def test_historical_data_from_activity_log(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(date="2026-02-20T12:00:00+00:00", duration_seconds=3000),
            _make_activity_record(date="2026-02-10T12:00:00+00:00", duration_seconds=3600),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool", history_count=5)

        assert len(result.historical) == 2

    async def test_statistical_summary(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(duration_seconds=2400),
            _make_activity_record(duration_seconds=3000),
            _make_activity_record(duration_seconds=3600),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        assert result.stats is not None
        assert result.stats.mean_duration_seconds > 0
        assert result.stats.p90_duration_seconds > 0

    async def test_anomaly_flag_when_exceeds_threshold(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_node_event("node-1", "NodeUpgrade", "2026-02-28T10:00:00+00:00"),
            # NodeReady much later — long upgrade
            _make_node_event("node-1", "NodeReady", "2026-02-28T11:30:00+00:00"),
        ]
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        # Total duration is 90 mins for one node, exceeds 60-minute threshold
        assert result.anomaly_flag is not None
        assert "60-minute" in result.anomaly_flag

    async def test_no_active_upgrade_history_only(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(duration_seconds=2400),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool")

        assert result.current_run is None
        assert len(result.historical) == 1

    async def test_fewer_historical_records(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = [
            _make_activity_record(duration_seconds=2400),
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            result = await get_upgrade_metrics_handler("prod-eastus", "userpool", history_count=5)

        assert len(result.historical) == 1
        assert "1 of 5" in result.summary

    async def test_cluster_all_fan_out(self) -> None:
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_aks = AsyncMock()
        mock_aks.get_activity_log_upgrades.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_metrics.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_metrics.AzureAksClient", return_value=mock_aks),
        ):
            from platform_mcp_server.tools.upgrade_metrics import get_upgrade_metrics_all

            results = await get_upgrade_metrics_all("userpool")

        assert len(results) == 6
