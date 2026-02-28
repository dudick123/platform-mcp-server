"""Kubernetes Core API wrapper — nodes, pods, namespaces."""

from __future__ import annotations

from typing import Any

import structlog
from kubernetes import client as k8s_client

from platform_mcp_server.clients import load_k8s_api_client
from platform_mcp_server.config import ClusterConfig

log = structlog.get_logger()

# Node pool label with fallback
# Note 1: Azure AKS historically used two different label keys to identify the node pool a VM
# Note 2: belongs to. Older clusters use the short "agentpool" label; newer ones use the fully
# Note 3: qualified "kubernetes.azure.com/agentpool". Checking the primary key first and falling
# Note 4: back to the secondary key lets this code work correctly across both generations without
# Note 5: requiring cluster-specific branching logic.
PRIMARY_POOL_LABEL = "agentpool"
FALLBACK_POOL_LABEL = "kubernetes.azure.com/agentpool"


class K8sCoreClient:
    """Wrapper around the Kubernetes Core V1 API."""

    # Note 6: The constructor accepts a ClusterConfig value object rather than raw strings so that
    # Note 7: callers cannot pass an inconsistent combination of context + subscription + region.
    # Note 8: The ClusterConfig is the single authoritative record for one cluster's identity.
    def __init__(self, cluster_config: ClusterConfig) -> None:
        self._cluster_config = cluster_config
        # Note 9: `_api` is initialised to None here rather than creating the CoreV1Api
        # Note 10: immediately. This is the lazy initialisation pattern: the real API client is
        # Note 11: only constructed the first time it is needed. Deferring creation avoids loading
        # Note 12: kubeconfig (a disk read) and instantiating HTTP connection pools at import time,
        # Note 13: which would slow startup and make unit tests that never call the API pay a
        # Note 14: needless overhead cost.
        self._api: k8s_client.CoreV1Api | None = None

    def _get_api(self) -> k8s_client.CoreV1Api:
        # Note 15: The None-check gate ensures the expensive setup runs exactly once per instance.
        # Note 16: Subsequent calls return the cached object without touching the filesystem again.
        if self._api is None:
            # Note 17: `load_k8s_api_client` constructs a per-context ApiClient rather than calling
            # Note 18: the global `kubernetes.config.load_kube_config()`. The global call mutates a
            # Note 19: module-level singleton, which is not safe when multiple K8sCoreClient instances
            # Note 20: for different clusters exist in the same process -- they would overwrite each
            # Note 21: other's context. The per-context approach is safe for concurrent multi-cluster use.
            api_client = load_k8s_api_client(self._cluster_config.kubeconfig_context)
            self._api = k8s_client.CoreV1Api(api_client)
        return self._api

    async def get_nodes(self) -> list[dict[str, Any]]:
        """List all nodes with pool grouping metadata.

        Returns a list of dicts with keys: name, pool, version, unschedulable,
        allocatable_cpu, allocatable_memory, conditions, labels.
        """
        api = self._get_api()
        try:
            node_list = api.list_node()
        except Exception:
            log.error("failed_to_list_nodes", cluster=self._cluster_config.cluster_id)
            raise

        results: list[dict[str, Any]] = []
        for node in node_list.items:
            labels = node.metadata.labels or {}
            # Note 22: The `or` short-circuit tries PRIMARY_POOL_LABEL first; if it returns None
            # Note 23: (key absent), it falls through to FALLBACK_POOL_LABEL. This is more concise
            # Note 24: than an explicit if/elif block and communicates the priority order clearly.
            pool = labels.get(PRIMARY_POOL_LABEL) or labels.get(FALLBACK_POOL_LABEL)
            if pool is None:
                log.warning(
                    "node_missing_pool_label",
                    node=node.metadata.name,
                    cluster=self._cluster_config.cluster_id,
                )

            allocatable = node.status.allocatable or {}
            # Note 25: The dict comprehension `{c.type: c.status for c in ...}` flattens the SDK's
            # Note 26: list of NodeCondition objects into a plain lookup map keyed by condition type
            # Note 27: (e.g., {"Ready": "True", "MemoryPressure": "False"}). This is much faster to
            # Note 28: query than scanning the list for a matching .type attribute on every access.
            conditions = {c.type: c.status for c in (node.status.conditions or [])}

            results.append(
                {
                    "name": node.metadata.name,
                    "pool": pool,
                    "version": (node.status.node_info.kubelet_version if node.status.node_info else None),
                    # Note 29: `bool(node.spec.unschedulable)` converts None (field absent, meaning
                    # Note 30: schedulable) and False to False, and True to True. Without the explicit
                    # Note 31: bool() call, None would appear in the output dict, which could confuse
                    # Note 32: downstream code that does a truthiness check vs an equality check.
                    "unschedulable": bool(node.spec.unschedulable),
                    "allocatable_cpu": allocatable.get("cpu", "0"),
                    "allocatable_memory": allocatable.get("memory", "0"),
                    "conditions": conditions,
                    "labels": labels,
                }
            )
        return results

    async def get_pods(
        self,
        namespace: str | None = None,
        field_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        """List pods with status details.

        Args:
            namespace: Filter to a specific namespace. None for all namespaces.
            field_selector: Kubernetes field selector string.

        Returns a list of pod dicts with key status fields.
        """
        api = self._get_api()
        try:
            # Note 33: The kwargs dict pattern accumulates optional parameters and unpacks them
            # Note 34: with **kwargs. This avoids writing four separate call-site permutations
            # Note 35: (field_selector yes/no crossed with namespace yes/no) and keeps the
            # Note 36: parameter-building logic in one readable block close to where it is used.
            kwargs: dict[str, Any] = {}
            if field_selector:
                # Note 37: field_selector is evaluated server-side by the Kubernetes API server
                # Note 38: before any data is sent over the network. Filtering here (e.g.,
                # Note 39: "status.phase=Pending") reduces the payload and avoids downloading
                # Note 40: running pods that the caller will discard immediately.
                kwargs["field_selector"] = field_selector
            if namespace:
                pod_list = api.list_namespaced_pod(namespace, **kwargs)
            else:
                pod_list = api.list_pod_for_all_namespaces(**kwargs)
        except Exception:
            log.error(
                "failed_to_list_pods",
                cluster=self._cluster_config.cluster_id,
                namespace=namespace,
            )
            raise

        results: list[dict[str, Any]] = []
        for pod in pod_list.items:
            container_statuses = []
            for cs in pod.status.container_statuses or []:
                cs_info: dict[str, Any] = {
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": {},
                }
                if cs.state:
                    if cs.state.waiting:
                        # Note 41: `cs.state.waiting` represents the CURRENT state: the container
                        # Note 42: has not yet started. Common reasons are "ContainerCreating" and
                        # Note 43: "ImagePullBackOff". Only one of waiting, running, or terminated
                        # Note 44: is set at a time -- the elif chain reflects that mutual exclusion.
                        cs_info["state"] = {"waiting": {"reason": cs.state.waiting.reason}}
                    elif cs.state.terminated:
                        cs_info["state"] = {
                            "terminated": {
                                "reason": cs.state.terminated.reason,
                                "exit_code": cs.state.terminated.exit_code,
                            }
                        }
                # Note 45: `cs.last_state.terminated` captures the PREVIOUS container run, not the
                # Note 46: current one. It is populated after a crash-restart cycle and provides the
                # Note 47: exit code and reason from the container that just died. This is critical
                # Note 48: for diagnosing OOMKilled or error-exit restart loops where the current
                # Note 49: state is "running" (the replacement container) but the root cause lives
                # Note 50: in last_state. Checking last_state separately preserves both signals.
                if cs.last_state and cs.last_state.terminated:
                    cs_info["last_terminated"] = {
                        "reason": cs.last_state.terminated.reason,
                        "exit_code": cs.last_state.terminated.exit_code,
                    }
                container_statuses.append(cs_info)

            results.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node_name": pod.spec.node_name,
                    "reason": pod.status.reason,
                    "message": pod.status.message,
                    "container_statuses": container_statuses,
                    # Note 51: The list comprehension over pod.status.conditions converts each
                    # Note 52: PodCondition SDK object into a plain dict. Normalising to plain dicts
                    # Note 53: here decouples the rest of the codebase from the kubernetes SDK's
                    # Note 54: object model -- callers can serialise, log, or compare conditions
                    # Note 55: without importing kubernetes types, making the data more portable.
                    "conditions": [
                        {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                        for c in (pod.status.conditions or [])
                    ],
                }
            )
        return results
