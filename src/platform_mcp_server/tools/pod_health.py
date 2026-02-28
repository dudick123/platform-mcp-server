"""get_pod_health — failed and pending pod diagnostics with failure reason grouping."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog

from platform_mcp_server.clients.k8s_core import K8sCoreClient
from platform_mcp_server.clients.k8s_events import K8sEventsClient
from platform_mcp_server.config import ALL_CLUSTER_IDS, resolve_cluster
from platform_mcp_server.models import PodDetail, PodHealthOutput, ToolError

log = structlog.get_logger()

RESULT_CAP = 50

# Failure category mapping
_SCHEDULING_REASONS = {"Unschedulable", "FailedScheduling", "InsufficientCPU", "InsufficientMemory"}
_RUNTIME_REASONS = {"CrashLoopBackOff", "OOMKilled", "Error", "ContainerStatusUnknown"}
_REGISTRY_REASONS = {"ImagePullBackOff", "ErrImagePull", "ErrImageNeverPull"}
_CONFIG_REASONS = {"CreateContainerConfigError", "InvalidImageName", "RunContainerError"}


def _categorize_failure(reason: str | None, container_statuses: list[dict[str, Any]]) -> str:
    """Determine failure category from reason and container state."""
    if reason and reason in _SCHEDULING_REASONS:
        return "scheduling"

    # Check container statuses for more specific reasons
    for cs in container_statuses:
        waiting = cs.get("state", {}).get("waiting", {})
        waiting_reason = waiting.get("reason", "")
        if waiting_reason in _SCHEDULING_REASONS:
            return "scheduling"
        if waiting_reason in _RUNTIME_REASONS:
            return "runtime"
        if waiting_reason in _REGISTRY_REASONS:
            return "registry"
        if waiting_reason in _CONFIG_REASONS:
            return "config"

        last_term = cs.get("last_terminated", {})
        if last_term.get("reason") == "OOMKilled":
            return "runtime"

    if reason and reason in _RUNTIME_REASONS:
        return "runtime"
    if reason and reason in _REGISTRY_REASONS:
        return "registry"
    if reason and reason in _CONFIG_REASONS:
        return "config"

    return "unknown"


def _is_unhealthy(pod: dict[str, Any]) -> bool:
    """Check if a pod is currently in an unhealthy state."""
    phase = pod.get("phase", "")
    if phase in ("Pending", "Failed", "Unknown"):
        return True

    # Check for CrashLoopBackOff or other bad states in running pods
    for cs in pod.get("container_statuses", []):
        waiting = cs.get("state", {}).get("waiting", {})
        if waiting.get("reason") in _RUNTIME_REASONS | _REGISTRY_REASONS | _CONFIG_REASONS:
            return True
        if cs.get("last_terminated", {}).get("reason") == "OOMKilled":
            return True

    return False


def _get_oomkill_info(container_statuses: list[dict[str, Any]]) -> tuple[str | None, str | None, int]:
    """Extract OOMKill container info."""
    for cs in container_statuses:
        last_term = cs.get("last_terminated", {})
        if last_term.get("reason") == "OOMKilled":
            return cs.get("name"), None, cs.get("restart_count", 0)
    return None, None, 0


async def get_pod_health_handler(
    cluster_id: str,
    namespace: str | None = None,
    status_filter: str = "all",
    lookback_minutes: int = 30,
) -> PodHealthOutput:
    """Core handler for get_pod_health on a single cluster."""
    config = resolve_cluster(cluster_id)
    core_client = K8sCoreClient(config)
    events_client = K8sEventsClient(config)
    errors: list[ToolError] = []

    pods = await core_client.get_pods(namespace=namespace)

    # Get pod events for context
    try:
        events = await events_client.get_pod_events(namespace=namespace)
    except Exception:
        events = []
        errors.append(
            ToolError(error="Failed to retrieve pod events", source="events-api", cluster=cluster_id, partial_data=True)
        )

    # Build event lookup: pod_name -> most recent event message
    event_map: dict[str, str] = {}
    for evt in events:
        pod_name = evt.get("pod_name", "")
        if pod_name:
            event_map[pod_name] = evt.get("message", "")

    # Filter to unhealthy pods
    unhealthy_pods = [p for p in pods if _is_unhealthy(p)]

    # Apply status_filter
    if status_filter == "pending":
        unhealthy_pods = [p for p in unhealthy_pods if p.get("phase") == "Pending"]
    elif status_filter == "failed":
        unhealthy_pods = [p for p in unhealthy_pods if p.get("phase") == "Failed"]

    total_matching = len(unhealthy_pods)

    # Build grouped counts (over all matching, not just capped)
    groups: dict[str, int] = defaultdict(int)
    for pod in unhealthy_pods:
        category = _categorize_failure(pod.get("reason"), pod.get("container_statuses", []))
        groups[category] += 1

    # Cap results
    truncated = total_matching > RESULT_CAP
    display_pods = unhealthy_pods[:RESULT_CAP]

    # Build pod details
    pod_details: list[PodDetail] = []
    for pod in display_pods:
        container_statuses = pod.get("container_statuses", [])
        category = _categorize_failure(pod.get("reason"), container_statuses)
        container_name, memory_limit, restart_count = _get_oomkill_info(container_statuses)

        # Sum up restart counts from all containers if not OOMKill
        if container_name is None:
            restart_count = sum(cs.get("restart_count", 0) for cs in container_statuses)

        pod_details.append(
            PodDetail(
                name=pod["name"],
                namespace=pod["namespace"],
                phase=pod.get("phase", "Unknown"),
                reason=pod.get("reason"),
                failure_category=category,
                restart_count=restart_count,
                last_event=event_map.get(pod["name"]),
                container_name=container_name,
                memory_limit=memory_limit,
            )
        )

    if truncated:
        summary = f"Showing {RESULT_CAP} of {total_matching} matching pods in {cluster_id}"
    elif total_matching > 0:
        summary = f"{total_matching} unhealthy pod{'s' if total_matching != 1 else ''} in {cluster_id}"
    else:
        summary = f"No unhealthy pods in {cluster_id}"

    return PodHealthOutput(
        cluster=cluster_id,
        pods=pod_details,
        groups=dict(groups),
        total_matching=total_matching,
        truncated=truncated,
        summary=summary,
        timestamp=datetime.now(tz=UTC).isoformat(),
        errors=errors,
    )


async def get_pod_health_all(
    namespace: str | None = None,
    status_filter: str = "all",
    lookback_minutes: int = 30,
) -> list[PodHealthOutput]:
    """Fan-out get_pod_health to all clusters concurrently."""
    tasks = [get_pod_health_handler(cid, namespace, status_filter, lookback_minutes) for cid in ALL_CLUSTER_IDS]
    return list(await asyncio.gather(*tasks, return_exceptions=False))
