"""Kubernetes Metrics API wrapper -- CPU/memory usage per node."""

# Note 1: `from __future__ import annotations` lets the type hint
# `k8s_client.CustomObjectsApi | None` be parsed as a string at definition time,
# avoiding a forward-reference error if the class is not yet fully resolved.
from __future__ import annotations

import asyncio
import threading
from typing import Any

import structlog
from kubernetes import client as k8s_client

from platform_mcp_server.clients import load_k8s_api_client
from platform_mcp_server.config import ClusterConfig

log = structlog.get_logger()


# Note 2: The Metrics API (metrics.k8s.io) is an *extension* API group, not part
# of the Kubernetes core/v1 REST surface.  It is served by a separate in-cluster
# component called metrics-server and registered via the API aggregation layer.
# Because of this, the standard generated client classes (CoreV1Api, AppsV1Api,
# etc.) have no knowledge of it; you must use CustomObjectsApi instead.
class K8sMetricsClient:
    """Wrapper around the Kubernetes Metrics API (metrics.k8s.io/v1beta1)."""

    def __init__(self, cluster_config: ClusterConfig) -> None:
        self._cluster_config = cluster_config
        # Note 3: The API client is stored as an optional attribute and created
        # lazily on first use (see _get_api below).  Deferring construction avoids
        # opening a network connection or reading kubeconfig until the client is
        # actually needed, which keeps object instantiation cheap and testable.
        self._api: k8s_client.CustomObjectsApi | None = None
        self._lock = threading.Lock()

    def _get_api(self) -> k8s_client.CustomObjectsApi:
        # Note 4: This is the lazy-initialization (or "lazy singleton") pattern.
        # The `if self._api is None` guard ensures the relatively expensive
        # load_k8s_api_client call -- which reads kubeconfig from disk -- happens
        # only once per K8sMetricsClient instance, not on every request.
        with self._lock:
            if self._api is None:
                api_client = load_k8s_api_client(self._cluster_config.kubeconfig_context)
                # Note 5: CustomObjectsApi is the correct class for any API group not
                # baked into the generated Kubernetes Python client.  It accepts raw
                # group/version/plural parameters and returns plain Python dicts rather
                # than typed model objects, matching what the aggregated Metrics API
                # actually returns over the wire.
                self._api = k8s_client.CustomObjectsApi(api_client)
            return self._api

    async def get_node_metrics(self) -> list[dict[str, Any]]:
        """Retrieve CPU and memory usage for all nodes via the Metrics API.

        Returns a list of dicts with keys: name, cpu_usage, memory_usage.
        Raises an exception if metrics-server is unavailable.
        """
        api = self._get_api()
        try:
            # Note 6: list_cluster_custom_object parameters map directly to the
            # Kubernetes REST path:  /apis/{group}/{version}/{plural}
            #   group   = "metrics.k8s.io"  -- the API extension group name
            #   version = "v1beta1"         -- the only stable version currently
            #   plural  = "nodes"           -- the resource kind in plural form
            # Using the plural resource name ("nodes" not "node") is required by
            # the Kubernetes API conventions for list endpoints.
            result = await asyncio.to_thread(
                api.list_cluster_custom_object,
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
        except Exception:
            # Note 7: Graceful degradation: if metrics-server is not installed in
            # the cluster (a common situation in dev or cost-constrained envs),
            # the API call raises an ApiException with status 404 or 503.  We log
            # a structured event so operators can distinguish "metrics unavailable"
            # from a genuine bug, then re-raise so callers can decide whether to
            # skip, surface a warning, or fail hard.
            log.error(
                "metrics_server_unavailable",
                cluster=self._cluster_config.cluster_id,
            )
            raise

        metrics: list[dict[str, Any]] = []
        for item in result.get("items", []):
            usage = item.get("usage", {})
            # Note 8: CPU usage is returned as a string in Kubernetes quantity
            # format (e.g. "125m" for 125 millicores) and memory as a string like
            # "512Mi".  We preserve them as raw strings here and leave any unit
            # conversion to the caller, keeping this layer a thin data accessor.
            metrics.append(
                {
                    "name": item["metadata"]["name"],
                    "cpu_usage": usage.get("cpu", "0"),
                    "memory_usage": usage.get("memory", "0"),
                }
            )
        return metrics
