"""Kubernetes Events API wrapper — NodeUpgrade, NodeReady, pod events."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Any

import structlog
from kubernetes import client as k8s_client

from platform_mcp_server.clients import load_k8s_api_client
from platform_mcp_server.config import ClusterConfig

log = structlog.get_logger()


class K8sEventsClient:
    """Wrapper around Kubernetes Events API for upgrade and pod event retrieval."""

    def __init__(self, cluster_config: ClusterConfig) -> None:
        self._cluster_config = cluster_config
        self._api: k8s_client.CoreV1Api | None = None
        self._lock = threading.Lock()

    def _get_api(self) -> k8s_client.CoreV1Api:
        with self._lock:
            if self._api is None:
                api_client = load_k8s_api_client(self._cluster_config.kubeconfig_context)
                self._api = k8s_client.CoreV1Api(api_client)
            return self._api

    async def get_node_events(
        self,
        # Note 1: `list[str] | None` is the idiomatic Python 3.10+ union for an
        # Note 2: optional parameter. `None` as the default means "no filter applied",
        # Note 3: which is cheaper to express at the call site than passing an empty list.
        reasons: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get events related to nodes, optionally filtered by reason.

        Args:
            reasons: Filter to specific event reasons (e.g., ['NodeUpgrade', 'NodeReady']).

        Returns a list of event dicts with timestamp, reason, node name, and message.
        """
        api = self._get_api()
        try:
            # Note 4: `list_event_for_all_namespaces` queries the /events endpoint
            # Note 5: across every namespace in a single API call. Node events are stored
            # Note 6: in kube-system (or sometimes "default") -- whichever namespace the
            # Note 7: control-plane component that generated the event lives in -- because
            # Note 8: a Kubernetes Event is always namespaced to the object it describes.
            # Note 9: The `field_selector` pushes the kind=Node filter to the API server
            # Note 10: so we avoid transferring every event type over the network.
            events = await asyncio.to_thread(
                api.list_event_for_all_namespaces,
                field_selector="involvedObject.kind=Node",
            )
        except Exception:
            log.error("failed_to_list_node_events", cluster=self._cluster_config.cluster_id)
            raise

        results: list[dict[str, Any]] = []
        for event in events.items:
            # Note 11: `event.reason` is a short, machine-readable token like "NodeUpgrade"
            # Note 12: or "NodeReady". `event.message` is the human-readable explanation.
            # Note 13: Filtering on `reason` (not `message`) keeps the logic stable against
            # Note 14: message wording changes across Kubernetes versions.
            if reasons and event.reason not in reasons:
                continue
            results.append(
                {
                    "reason": event.reason,
                    "node_name": event.involved_object.name,
                    "message": event.message,
                    "timestamp": _event_timestamp(event),
                    "count": event.count,
                }
            )
        return results

    async def get_pod_events(
        self,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get events related to pods.

        Args:
            namespace: Filter to a specific namespace. None for all namespaces.

        Returns a list of event dicts with timestamp, reason, pod name, and message.
        """
        api = self._get_api()
        try:
            if namespace:
                # Note 15: When a namespace is known, prefer `list_namespaced_event` because
                # Note 16: it targets a narrower API path (/namespaces/{ns}/events) and avoids
                # Note 17: fetching events from unrelated namespaces. Pod events are always
                # Note 18: stored in the same namespace as the pod itself -- never in kube-system.
                events = await asyncio.to_thread(
                    api.list_namespaced_event,
                    namespace,
                    field_selector="involvedObject.kind=Pod",
                )
            else:
                events = await asyncio.to_thread(
                    api.list_event_for_all_namespaces,
                    field_selector="involvedObject.kind=Pod",
                )
        except Exception:
            log.error(
                "failed_to_list_pod_events",
                cluster=self._cluster_config.cluster_id,
                namespace=namespace,
            )
            raise

        results: list[dict[str, Any]] = []
        for event in events.items:
            results.append(
                {
                    "reason": event.reason,
                    "pod_name": event.involved_object.name,
                    # Note 19: `involved_object.namespace` is included here even though we may
                    # Note 20: have already filtered by namespace, because when querying all
                    # Note 21: namespaces callers need provenance to correlate events with pods.
                    "namespace": event.involved_object.namespace,
                    "message": event.message,
                    "timestamp": _event_timestamp(event),
                    "count": event.count,
                }
            )
        return results


def _event_timestamp(event: Any) -> str | None:
    """Extract the most relevant timestamp from a Kubernetes event."""
    # Note 22: Kubernetes events carry three timestamp fields with different semantics:
    # Note 23:   last_timestamp -- updated each time the event recurs (most informative).
    # Note 24:   event_time    -- set by newer Event v1 objects; maps to EventSeries.
    # Note 25:   first_timestamp -- when the event was first observed (least useful for
    # Note 26:                      recurrence tracking, but better than nothing).
    # Note 27: The `or` chain picks the first truthy value, implementing priority order.
    ts = event.last_timestamp or event.event_time or event.first_timestamp
    if isinstance(ts, datetime):
        # Note 28: `.isoformat()` produces RFC 3339 strings (e.g., "2024-06-01T12:00:00+00:00")
        # Note 29: which LLMs and downstream JSON consumers parse unambiguously without
        # Note 30: needing to know epoch offsets or locale-specific date formats.
        return ts.isoformat()
    return str(ts) if ts else None
