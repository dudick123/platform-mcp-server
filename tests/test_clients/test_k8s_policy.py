# Note 1: This file tests K8sPolicyClient, which wraps the Kubernetes policy/v1 API for
# PodDisruptionBudgets (PDBs). PDBs are a safety mechanism that limits how many pods can
# be simultaneously unavailable during voluntary disruptions (like node drain or upgrades).
# The two test classes here cover listing PDBs and evaluating whether they block a drain.
"""Tests for K8sPolicyClient: PDB listing, disruption budget evaluation, satisfiability."""

# Note 2: The `from __future__ import annotations` import is especially useful in this
# file because the `_make_mock_pdb` factory uses `int | str | None` union types in its
# signature. PEP 604 union syntax (`X | Y`) for type hints in function signatures requires
# Python 3.10+ at runtime without this import; with it, annotations are evaluated lazily
# as strings and work on Python 3.9+ as well.
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.k8s_policy import K8sPolicyClient
from platform_mcp_server.config import CLUSTER_MAP


# Note 3: PDB spec fields min_available and max_unavailable accept either an integer
# (absolute pod count) or a string (percentage like "50%"). Both types are valid in the
# Kubernetes API, and production code must handle both. The factory signature uses
# `int | str | None` to communicate this polymorphism to readers and to enable tests
# to pass both integer and string values without type errors.
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
    # Note 4: min_available and max_unavailable are set on `pdb.spec` (the desired
    # configuration) while current_healthy and disruptions_allowed are set on `pdb.status`
    # (the observed runtime state). This mirrors the Kubernetes object structure where
    # spec is the desired state and status is the observed state. The test factory mirrors
    # this separation to keep mock data semantically accurate.
    pdb.spec.min_available = min_available
    pdb.spec.max_unavailable = max_unavailable
    # Note 5: `match_labels or {"app": "my-app"}` uses the `or` short-circuit to provide
    # a default label selector when None is passed. A PDB without a label selector would
    # be invalid in Kubernetes, so a meaningful default makes the mock structurally valid
    # even when the test does not care about the selector value.
    pdb.spec.selector.match_labels = match_labels or {"app": "my-app"}
    pdb.status.current_healthy = current_healthy
    pdb.status.desired_healthy = desired_healthy
    pdb.status.disruptions_allowed = disruptions_allowed
    pdb.status.expected_pods = expected_pods
    return pdb


# Note 6: The client fixture provides a fresh K8sPolicyClient for each test. Because
# K8sPolicyClient (like all clients) uses lazy initialization, `_api` is None at the
# start of each test. The `patch.object(client, "_get_api", ...)` calls inside tests
# ensure the real Kubernetes API is never contacted.
@pytest.fixture
def client() -> K8sPolicyClient:
    return K8sPolicyClient(CLUSTER_MAP["prod-eastus"])


class TestGetPdbs:
    async def test_returns_pdbs_all_namespaces(self, client: K8sPolicyClient) -> None:
        # Note 7: Two PDBs with different constraint types (min_available vs max_unavailable)
        # are returned in this test. Using both constraint types in the same test verifies
        # the client serializes both fields correctly into the output dict rather than
        # only handling one constraint type.
        mock_api = MagicMock()
        pdb_list = MagicMock()
        pdb_list.items = [
            _make_mock_pdb(name="pdb-1", min_available=2, disruptions_allowed=1),
            _make_mock_pdb(name="pdb-2", max_unavailable=0, disruptions_allowed=0),
        ]
        mock_api.list_pod_disruption_budget_for_all_namespaces.return_value = pdb_list

        # Note 8: `patch.object` is called with `return_value=mock_api` rather than
        # `return_value=MagicMock()` assigned to `mock_api.return_value`. Both work, but
        # `return_value=mock_api` is more explicit: it sets what `_get_api()` returns
        # directly at the patch site, making the test setup easier to trace.
        with patch.object(client, "_get_api", return_value=mock_api):
            pdbs = await client.get_pdbs()

        assert len(pdbs) == 2
        assert pdbs[0]["name"] == "pdb-1"
        # Note 9: min_available=2 is asserted with an integer (not a string) because the
        # factory sets it as an integer. If the production code accidentally stringified
        # the value, `pdbs[0]["min_available"] == 2` would fail (since "2" != 2 in Python).
        # This type-sensitive assertion catches accidental type coercion.
        assert pdbs[0]["min_available"] == 2
        assert pdbs[1]["max_unavailable"] == 0

    async def test_returns_pdbs_filtered_namespace(self, client: K8sPolicyClient) -> None:
        # Note 10: When a namespace is provided, the client should use
        # `list_namespaced_pod_disruption_budget` instead of the cluster-wide version.
        # The `assert_called_once_with("payments")` assertion verifies the namespace
        # argument is forwarded to the API call, confirming the client does not silently
        # ignore the namespace filter and return all PDBs.
        mock_api = MagicMock()
        pdb_list = MagicMock()
        pdb_list.items = [_make_mock_pdb(name="pdb-1", namespace="payments")]
        mock_api.list_namespaced_pod_disruption_budget.return_value = pdb_list

        with patch.object(client, "_get_api", return_value=mock_api):
            pdbs = await client.get_pdbs(namespace="payments")

        assert len(pdbs) == 1
        mock_api.list_namespaced_pod_disruption_budget.assert_called_once_with("payments")

    async def test_error_handling(self, client: K8sPolicyClient) -> None:
        # Note 11: "Forbidden" simulates a Kubernetes RBAC authorization failure. RBAC
        # errors are common in multi-tenant clusters where service accounts may not have
        # permission to list PDBs across all namespaces. Testing this error path ensures
        # the client surfaces the authorization failure rather than returning an empty list,
        # which would be a silent failure hiding a permissions misconfiguration.
        mock_api = MagicMock()
        mock_api.list_pod_disruption_budget_for_all_namespaces.side_effect = Exception("Forbidden")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="Forbidden"):
            await client.get_pdbs()


# Note 12: TestEvaluatePdbSatisfiability tests a pure-logic method that takes a list of
# dicts (already-serialized PDB data) and returns the subset that would block a node drain.
# Because this method operates on plain Python dicts rather than Kubernetes API objects,
# no API mocking is needed — the tests pass data directly to the method. This makes the
# satisfiability tests faster and simpler than the API-bound listing tests above.
class TestEvaluatePdbSatisfiability:
    async def test_max_unavailable_zero_flagged(self, client: K8sPolicyClient) -> None:
        # Note 13: `max_unavailable=0` is the strictest possible PDB constraint: zero pods
        # may be unavailable at any time. This absolutely blocks a node drain because
        # draining a node requires evicting all its pods, making them temporarily unavailable.
        # The test verifies the client identifies this PDB as a blocker and returns a
        # dict with the "block_reason" key set to "maxUnavailable=0".
        pdbs = [
            {"name": "pdb-1", "namespace": "default", "max_unavailable": 0, "disruptions_allowed": 0},
        ]
        blockers = await client.evaluate_pdb_satisfiability(pdbs)
        assert len(blockers) == 1
        assert blockers[0]["block_reason"] == "maxUnavailable=0"

    async def test_min_available_equals_ready_flagged(self, client: K8sPolicyClient) -> None:
        # Note 14: `min_available=3` with `current_healthy=3` means all pods must be
        # healthy at all times — evicting even one pod would drop current_healthy below
        # min_available, violating the PDB. This is the "tight" scenario where the cluster
        # is running at exactly the minimum required capacity. The `in` check on block_reason
        # allows for message variations as long as "minAvailable=3" appears somewhere in it.
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
        # Note 15: `disruptions_allowed=2` with `current_healthy=4` and `min_available=2`
        # represents a PDB with comfortable headroom. Even if two pods are evicted, the
        # remaining two satisfy min_available. This test verifies the "happy path" where
        # the evaluator correctly identifies that a drain CAN proceed and returns no blockers.
        # An empty blockers list means the cluster is safe to drain.
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
        # Note 16: This test exercises the most realistic production scenario: a cluster
        # with a mix of blocking and non-blocking PDBs. Three PDBs are evaluated:
        # "blocker" (max_unavailable=0), "safe" (has budget), and "tight" (min_available
        # equals current_healthy). Only "blocker" and "tight" should appear in the result.
        # Using a set comprehension for `names` makes the assertion order-independent.
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
        # Note 17: Asserting `len(blockers) == 2` before checking the set of names
        # provides a clearer failure message if the count is wrong. If the count assertion
        # fails, you immediately know how many blockers were returned. If only the set
        # assertion were used, a count of 3 would pass the set check (since both expected
        # names are still present) but represent incorrect behavior.
        assert len(blockers) == 2
        names = {b["name"] for b in blockers}
        assert names == {"blocker", "tight"}
