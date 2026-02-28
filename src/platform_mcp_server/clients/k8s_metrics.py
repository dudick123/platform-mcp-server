"""Kubernetes Metrics API wrapper — CPU/memory usage per node."""

from __future__ import annotations

from typing import Any

import structlog
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from platform_mcp_server.config import ClusterConfig

log = structlog.get_logger()


class K8sMetricsClient:
    """Wrapper around the Kubernetes Metrics API (metrics.k8s.io/v1beta1)."""

    def __init__(self, cluster_config: ClusterConfig) -> None:
        self._cluster_config = cluster_config
        self._api: k8s_client.CustomObjectsApi | None = None

    def _get_api(self) -> k8s_client.CustomObjectsApi:
        if self._api is None:
            k8s_config.load_kube_config(context=self._cluster_config.kubeconfig_context)
            configuration = k8s_client.Configuration.get_default_copy()
            api_client = k8s_client.ApiClient(configuration)
            self._api = k8s_client.CustomObjectsApi(api_client)
        return self._api

    async def get_node_metrics(self) -> list[dict[str, Any]]:
        """Retrieve CPU and memory usage for all nodes via the Metrics API.

        Returns a list of dicts with keys: name, cpu_usage, memory_usage.
        Raises an exception if metrics-server is unavailable.
        """
        api = self._get_api()
        try:
            result = api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
        except Exception:
            log.error(
                "metrics_server_unavailable",
                cluster=self._cluster_config.cluster_id,
            )
            raise

        metrics: list[dict[str, Any]] = []
        for item in result.get("items", []):
            usage = item.get("usage", {})
            metrics.append(
                {
                    "name": item["metadata"]["name"],
                    "cpu_usage": usage.get("cpu", "0"),
                    "memory_usage": usage.get("memory", "0"),
                }
            )
        return metrics
