"""Kubernetes Core API wrapper — nodes, pods, namespaces."""

from __future__ import annotations

from typing import Any

import structlog
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from platform_mcp_server.config import ClusterConfig

log = structlog.get_logger()

# Node pool label with fallback
PRIMARY_POOL_LABEL = "agentpool"
FALLBACK_POOL_LABEL = "kubernetes.azure.com/agentpool"


class K8sCoreClient:
    """Wrapper around the Kubernetes Core V1 API."""

    def __init__(self, cluster_config: ClusterConfig) -> None:
        self._cluster_config = cluster_config
        self._api: k8s_client.CoreV1Api | None = None

    def _get_api(self) -> k8s_client.CoreV1Api:
        if self._api is None:
            api_client = _load_k8s_client(self._cluster_config.kubeconfig_context)
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
            pool = labels.get(PRIMARY_POOL_LABEL) or labels.get(FALLBACK_POOL_LABEL)
            if pool is None:
                log.warning(
                    "node_missing_pool_label",
                    node=node.metadata.name,
                    cluster=self._cluster_config.cluster_id,
                )

            allocatable = node.status.allocatable or {}
            conditions = {c.type: c.status for c in (node.status.conditions or [])}

            results.append(
                {
                    "name": node.metadata.name,
                    "pool": pool,
                    "version": (node.status.node_info.kubelet_version if node.status.node_info else None),
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
            kwargs: dict[str, Any] = {}
            if field_selector:
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
                        cs_info["state"] = {"waiting": {"reason": cs.state.waiting.reason}}
                    elif cs.state.terminated:
                        cs_info["state"] = {
                            "terminated": {
                                "reason": cs.state.terminated.reason,
                                "exit_code": cs.state.terminated.exit_code,
                            }
                        }
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
                    "conditions": [
                        {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                        for c in (pod.status.conditions or [])
                    ],
                }
            )
        return results


def _load_k8s_client(context: str) -> k8s_client.ApiClient:
    """Load a Kubernetes API client for the given kubeconfig context."""
    k8s_config.load_kube_config(context=context)
    configuration = k8s_client.Configuration.get_default_copy()
    return k8s_client.ApiClient(configuration)
