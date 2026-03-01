"""Kubernetes Policy API wrapper — PodDisruptionBudgets."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import structlog
from kubernetes import client as k8s_client

from platform_mcp_server.clients import load_k8s_api_client
from platform_mcp_server.config import ClusterConfig

log = structlog.get_logger()


# Note 1: PodDisruptionBudgets (PDBs) let application owners set a lower bound on
# Note 2: pod availability during *voluntary* disruptions -- draining a node for
# Note 3: an upgrade is a voluntary disruption. If evicting a pod would violate
# Note 4: the PDB, the Kubernetes eviction API returns HTTP 429 and the drain blocks.
class K8sPolicyClient:
    """Wrapper around the Kubernetes Policy V1 API for PDB operations."""

    def __init__(self, cluster_config: ClusterConfig) -> None:
        self._cluster_config = cluster_config
        # Note 5: `PolicyV1Api` corresponds to the `policy/v1` API group, which
        # Note 6: graduated from `policy/v1beta1` in Kubernetes 1.21. Using the
        # Note 7: stable v1 group avoids deprecation warnings on modern clusters.
        self._api: k8s_client.PolicyV1Api | None = None
        self._lock = threading.Lock()

    def _get_api(self) -> k8s_client.PolicyV1Api:
        with self._lock:
            if self._api is None:
                api_client = load_k8s_api_client(self._cluster_config.kubeconfig_context)
                self._api = k8s_client.PolicyV1Api(api_client)
            return self._api

    async def get_pdbs(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """List all PodDisruptionBudgets.

        Args:
            namespace: Filter to a specific namespace. None for all namespaces.

        Returns a list of PDB dicts with spec and status fields.
        """
        api = self._get_api()
        try:
            if namespace:
                # Note 8: `list_namespaced_pod_disruption_budget` scopes the request to a
                # Note 9: single namespace when the caller already knows the target namespace,
                # Note 10: reducing the response payload and API server load.
                pdb_list = await asyncio.to_thread(api.list_namespaced_pod_disruption_budget, namespace)
            else:
                # Note 11: `list_pod_disruption_budget_for_all_namespaces` is the cluster-wide
                # Note 12: variant. It is equivalent to `kubectl get pdb -A` and is preferred
                # Note 13: when building an upgrade safety check that must inspect every PDB.
                pdb_list = await asyncio.to_thread(api.list_pod_disruption_budget_for_all_namespaces)
        except Exception:
            log.error("failed_to_list_pdbs", cluster=self._cluster_config.cluster_id)
            raise

        results: list[dict[str, Any]] = []
        for pdb in pdb_list.items:
            spec = pdb.spec
            status = pdb.status

            results.append(
                {
                    "name": pdb.metadata.name,
                    "namespace": pdb.metadata.namespace,
                    # Note 14: A PDB author sets *either* `minAvailable` or `maxUnavailable`,
                    # Note 15: never both. The Kubernetes API stores whichever field was set and
                    # Note 16: leaves the other as None. The None guard here prevents a
                    # Note 17: misleading "0" from appearing for the field that was never set.
                    "min_available": _int_or_str(spec.min_available) if spec.min_available is not None else None,
                    "max_unavailable": (
                        _int_or_str(spec.max_unavailable) if spec.max_unavailable is not None else None
                    ),
                    "selector": spec.selector.match_labels if spec.selector and spec.selector.match_labels else {},
                    "current_healthy": status.current_healthy if status else 0,
                    "desired_healthy": status.desired_healthy if status else 0,
                    # Note 18: `disruptions_allowed` is the real-time eviction headroom computed
                    # Note 19: by the PDB controller: current_healthy - desired_healthy (roughly).
                    # Note 20: A value of 0 means no pods can be evicted right now without
                    # Note 21: breaching the budget, regardless of which spec field was used.
                    "disruptions_allowed": status.disruptions_allowed if status else 0,
                    "expected_pods": status.expected_pods if status else 0,
                }
            )
        return results

    async def evaluate_pdb_satisfiability(
        self,
        pdbs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Evaluate which PDBs would block drain operations.

        A PDB blocks drain when:
        - maxUnavailable=0, OR
        - disruptions_allowed=0 (minAvailable equals current ready count)

        Args:
            pdbs: List of PDB dicts from get_pdbs().

        Returns a list of PDB dicts that would block drain, with a 'block_reason' field.
        """
        blockers: list[dict[str, Any]] = []
        for pdb in pdbs:
            max_unavailable = pdb.get("max_unavailable")
            disruptions_allowed = pdb.get("disruptions_allowed", 0)

            # Note 22: `maxUnavailable=0` is a hard block: the author explicitly declared
            # Note 23: that zero pods may be unavailable at any time. Even if all pods are
            # Note 24: healthy, evicting one would immediately violate the budget.
            if max_unavailable == 0:
                blockers.append({**pdb, "block_reason": "maxUnavailable=0"})
            # Note 25: `disruptions_allowed=0` catches the `minAvailable` case: the PDB
            # Note 26: controller has determined that the current number of healthy pods
            # Note 27: exactly meets (or is below) the minimum, so no eviction is safe.
            # Note 28: `{**pdb, ...}` unpacks the existing dict and merges in the new key,
            # Note 29: producing a shallow copy rather than mutating the caller's data.
            elif disruptions_allowed == 0:
                blockers.append(
                    {
                        **pdb,
                        "block_reason": (
                            f"minAvailable={pdb.get('min_available')} equals current healthy count "
                            f"({pdb.get('current_healthy')})"
                        ),
                    }
                )
        return blockers


def _int_or_str(value: Any) -> int | str:
    """Convert a Kubernetes IntOrString value to int or str."""
    # Note 30: Kubernetes IntOrString fields accept either a plain integer (e.g., 2)
    # Note 31: or a percentage string (e.g., "25%"). The Python client may deserialize
    # Note 32: these as int or str depending on what was stored in the manifest.
    # Note 33: Attempting `int(value)` handles string digits like "2" that should be
    # Note 34: treated as counts. ValueError/TypeError preserves genuine percentages
    # Note 35: like "25%" as strings so callers can display them without confusion.
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):  # fmt: skip
        return str(value)
