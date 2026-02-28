"""Tests for K8sPolicyClient: PDB listing, disruption budget evaluation, satisfiability."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.k8s_policy import K8sPolicyClient
from platform_mcp_server.config import CLUSTER_MAP


def _make_mock_pdb(
    name: str = "my-pdb",
    namespace: str = "default",
    min_available: int | str | None = None,
    max_unavailable: int | str | None = None,
    current_healthy: int = 3,
    desired_healthy: int = 3,
    disruptions_allowed: int = 0,
    expected_pods: int = 3,
    match_labels: dict[str, str] | None = None,
) -> MagicMock:
    pdb = MagicMock()
    pdb.metadata.name = name
    pdb.metadata.namespace = namespace
    pdb.spec.min_available = min_available
    pdb.spec.max_unavailable = max_unavailable
    pdb.spec.selector.match_labels = match_labels or {"app": "my-app"}
    pdb.status.current_healthy = current_healthy
    pdb.status.desired_healthy = desired_healthy
    pdb.status.disruptions_allowed = disruptions_allowed
    pdb.status.expected_pods = expected_pods
    return pdb


@pytest.fixture
def client() -> K8sPolicyClient:
    return K8sPolicyClient(CLUSTER_MAP["prod-eastus"])


class TestGetPdbs:
    async def test_returns_pdbs_all_namespaces(self, client: K8sPolicyClient) -> None:
        mock_api = MagicMock()
        pdb_list = MagicMock()
        pdb_list.items = [
            _make_mock_pdb(name="pdb-1", min_available=2, disruptions_allowed=1),
            _make_mock_pdb(name="pdb-2", max_unavailable=0, disruptions_allowed=0),
        ]
        mock_api.list_pod_disruption_budget_for_all_namespaces.return_value = pdb_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pdbs = await client.get_pdbs()

        assert len(pdbs) == 2
        assert pdbs[0]["name"] == "pdb-1"
        assert pdbs[0]["min_available"] == 2
        assert pdbs[1]["max_unavailable"] == 0

    async def test_returns_pdbs_filtered_namespace(self, client: K8sPolicyClient) -> None:
        mock_api = MagicMock()
        pdb_list = MagicMock()
        pdb_list.items = [_make_mock_pdb(name="pdb-1", namespace="payments")]
        mock_api.list_namespaced_pod_disruption_budget.return_value = pdb_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pdbs = await client.get_pdbs(namespace="payments")

        assert len(pdbs) == 1
        mock_api.list_namespaced_pod_disruption_budget.assert_called_once_with("payments")

    async def test_error_handling(self, client: K8sPolicyClient) -> None:
        mock_api = MagicMock()
        mock_api.list_pod_disruption_budget_for_all_namespaces.side_effect = Exception("Forbidden")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Forbidden"):
            await client.get_pdbs()


class TestEvaluatePdbSatisfiability:
    async def test_max_unavailable_zero_flagged(self, client: K8sPolicyClient) -> None:
        pdbs = [
            {"name": "pdb-1", "namespace": "default", "max_unavailable": 0, "disruptions_allowed": 0},
        ]
        blockers = await client.evaluate_pdb_satisfiability(pdbs)
        assert len(blockers) == 1
        assert blockers[0]["block_reason"] == "maxUnavailable=0"

    async def test_min_available_equals_ready_flagged(self, client: K8sPolicyClient) -> None:
        pdbs = [
            {
                "name": "pdb-1",
                "namespace": "default",
                "max_unavailable": None,
                "min_available": 3,
                "current_healthy": 3,
                "disruptions_allowed": 0,
            },
        ]
        blockers = await client.evaluate_pdb_satisfiability(pdbs)
        assert len(blockers) == 1
        assert "minAvailable=3" in blockers[0]["block_reason"]

    async def test_available_budget_not_flagged(self, client: K8sPolicyClient) -> None:
        pdbs = [
            {
                "name": "pdb-1",
                "namespace": "default",
                "max_unavailable": 1,
                "min_available": 2,
                "current_healthy": 4,
                "disruptions_allowed": 2,
            },
        ]
        blockers = await client.evaluate_pdb_satisfiability(pdbs)
        assert len(blockers) == 0

    async def test_mixed_pdbs(self, client: K8sPolicyClient) -> None:
        pdbs = [
            {"name": "blocker", "namespace": "ns1", "max_unavailable": 0, "disruptions_allowed": 0},
            {"name": "safe", "namespace": "ns2", "max_unavailable": 1, "disruptions_allowed": 1},
            {
                "name": "tight",
                "namespace": "ns3",
                "max_unavailable": None,
                "min_available": 5,
                "current_healthy": 5,
                "disruptions_allowed": 0,
            },
        ]
        blockers = await client.evaluate_pdb_satisfiability(pdbs)
        assert len(blockers) == 2
        names = {b["name"] for b in blockers}
        assert names == {"blocker", "tight"}
