"""Tests for check_pdb_upgrade_risk tool handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.pdb_check import check_pdb_risk_handler


def _make_pdb(
    name: str = "my-pdb",
    namespace: str = "default",
    max_unavailable: int | None = None,
    min_available: int | None = None,
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
        "desired_healthy": current_healthy,
        "disruptions_allowed": disruptions_allowed,
        "expected_pods": expected_pods,
        "selector": selector or {"app": "my-app"},
    }


def _make_node(name: str, pool: str = "userpool", unschedulable: bool = False) -> dict:
    return {
        "name": name,
        "pool": pool,
        "version": "v1.29.8",
        "unschedulable": unschedulable,
        "allocatable_cpu": "4000m",
        "allocatable_memory": "16Gi",
        "conditions": {"Ready": "True"},
        "labels": {"agentpool": pool},
    }


class TestCheckPdbRiskPreflight:
    async def test_max_unavailable_zero_flagged(self) -> None:
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [_make_pdb(name="tight-pdb", max_unavailable=0)]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {**_make_pdb(name="tight-pdb", max_unavailable=0), "block_reason": "maxUnavailable=0"}
        ]
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = []

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="preflight")

        assert len(result.risks) == 1
        assert "maxUnavailable=0" in result.risks[0].reason

    async def test_min_available_equals_ready_flagged(self) -> None:
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
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            _make_pdb(name="safe-pdb", min_available=2, current_healthy=4, disruptions_allowed=2)
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = []
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = []

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="preflight")

        assert len(result.risks) == 0


class TestCheckPdbRiskLive:
    async def test_active_block_on_cordoned_node(self) -> None:
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [_make_pdb(name="blocking-pdb", max_unavailable=0, disruptions_allowed=0)]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {**_make_pdb(name="blocking-pdb", max_unavailable=0), "block_reason": "maxUnavailable=0"}
        ]
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", unschedulable=True)]

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="live")

        assert len(result.risks) >= 1
        assert result.mode == "live"

    async def test_no_active_blocks(self) -> None:
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            _make_pdb(name="safe-pdb", min_available=2, current_healthy=4, disruptions_allowed=2)
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = []
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", unschedulable=True)]

        with (
            patch("platform_mcp_server.tools.pdb_check.K8sPolicyClient", return_value=mock_policy),
            patch("platform_mcp_server.tools.pdb_check.K8sCoreClient", return_value=mock_core),
        ):
            result = await check_pdb_risk_handler("prod-eastus", mode="live")

        assert len(result.risks) == 0


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
            from platform_mcp_server.tools.pdb_check import check_pdb_risk_all

            results = await check_pdb_risk_all()

        assert len(results) == 6
