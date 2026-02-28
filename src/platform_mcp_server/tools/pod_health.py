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
from platform_mcp_server.tools.pod_classification import categorize_failure, is_unhealthy
from platform_mcp_server.validation import validate_namespace

log = structlog.get_logger()

# Note 1: RESULT_CAP limits how many pod detail objects are sent to the LLM.
# Note 2: Large clusters can have hundreds of unhealthy pods; without a cap the
# Note 3: context window would be overwhelmed, degrading response quality and speed.
# Note 4: Grouping counts (built over ALL matching pods) are still reported in full,
# Note 5: so the LLM has accurate aggregate data even when individual pod details are capped.
RESULT_CAP = 50


# Note 6: OOMKilled is a container termination reason, not a waiting reason.
# Note 7: After a container is OOM-killed, Kubernetes records the event in
# Note 8: "last_terminated" (the previous container run), not in "state.waiting".
# Note 9: Checking state.waiting.reason for "OOMKilled" would always miss it; the
# Note 10: correct field to inspect is container_status["last_terminated"]["reason"].
def _get_oomkill_info(container_statuses: list[dict[str, Any]]) -> tuple[str | None, str | None, int]:
    """Extract OOMKill container info."""
    for cs in container_statuses:
        last_term = cs.get("last_terminated", {})
        if last_term.get("reason") == "OOMKilled":
            # Note 11: Returns the specific container name so callers can report WHICH
            # Note 12: container in a multi-container pod was killed, not just the pod.
            return cs.get("name"), None, cs.get("restart_count", 0)
    # Note 13: Returning (None, None, 0) signals "no OOMKill detected"; the caller
    # Note 14: uses container_name is None as the sentinel to switch to summed restarts.
    return None, None, 0


async def get_pod_health_handler(
    cluster_id: str,
    namespace: str | None = None,
    status_filter: str = "all",
    lookback_minutes: int = 30,
) -> PodHealthOutput:
    """Core handler for get_pod_health on a single cluster."""
    # Note 15: validate_namespace is called before any API calls so an invalid input
    # Note 16: raises immediately (fail-fast), avoiding unnecessary network round trips.
    validate_namespace(namespace)
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
    # Note 17: event_map is constructed once outside the pod loop so each pod detail
    # Note 18: can look up its most recent event in O(1) via dict key access.
    # Note 19: Building this map before the loop avoids an O(n*m) nested scan where
    # Note 20: n=pods and m=events, keeping total complexity O(n + m).
    event_map: dict[str, str] = {}
    for evt in events:
        pod_name = evt.get("pod_name", "")
        if pod_name:
            # Note 21: Later events in the list overwrite earlier ones for the same pod,
            # Note 22: so event_map naturally retains the most recent event message per pod.
            event_map[pod_name] = evt.get("message", "")

    # Filter to unhealthy pods
    # Note 23: is_unhealthy is applied as a pre-filter before any grouping or capping.
    # Note 24: This ensures that groups and total_matching count reflect the real universe
    # Note 25: of unhealthy pods, not just the capped display subset.
    unhealthy_pods = [p for p in pods if is_unhealthy(p)]

    # Apply status_filter
    # Note 26: status_filter is a three-way branch: "all" keeps every unhealthy pod,
    # Note 27: "pending" keeps only Pending-phase pods (not yet running), and "failed"
    # Note 28: keeps only Failed-phase pods (terminated with non-zero exit or eviction).
    if status_filter == "pending":
        unhealthy_pods = [p for p in unhealthy_pods if p.get("phase") == "Pending"]
    elif status_filter == "failed":
        unhealthy_pods = [p for p in unhealthy_pods if p.get("phase") == "Failed"]

    total_matching = len(unhealthy_pods)

    # Build grouped counts (over all matching, not just capped)
    # Note 29: Grouping runs over the full unhealthy_pods list BEFORE capping so that
    # Note 30: the "groups" breakdown in the output reflects the true cluster state,
    # Note 31: not merely the first 50 pods that happen to appear in the API response.
    groups: dict[str, int] = defaultdict(int)
    for pod in unhealthy_pods:
        category = categorize_failure(pod.get("reason"), pod.get("container_statuses", []))
        groups[category] += 1

    # Cap results
    # Note 32: Truncation is detected before slicing so that the summary message can
    # Note 33: accurately state the true count vs the displayed count.
    truncated = total_matching > RESULT_CAP
    display_pods = unhealthy_pods[:RESULT_CAP]

    # Build pod details
    pod_details: list[PodDetail] = []
    for pod in display_pods:
        container_statuses = pod.get("container_statuses", [])
        category = categorize_failure(pod.get("reason"), container_statuses)
        container_name, memory_limit, restart_count = _get_oomkill_info(container_statuses)

        # Sum up restart counts from all containers if not OOMKill
        # Note 34: container_name is None means _get_oomkill_info found no OOMKill event.
        # Note 35: In that case, restart_count from _get_oomkill_info is 0 (meaningless),
        # Note 36: so we replace it with the sum across all containers in the pod.
        # Note 37: For OOMKill pods, the per-container restart_count from last_terminated
        # Note 38: is already the correct and most informative value, so it is kept as-is.
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

    # Note 39: The summary string follows four cases to give the LLM a clear, human-readable
    # Note 40: one-liner: truncated (showing N of M), multiple (N unhealthy pods),
    # Note 41: single (1 unhealthy pod -- avoids "1 pods"), and zero (no unhealthy pods).
    # Note 42: The conditional plural suffix ('s' if ... != 1 else '') is a common Python
    # Note 43: idiom for grammatically correct singular/plural without importing inflect libs.
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
    # Note 44: asyncio.gather(*tasks, return_exceptions=True) launches all cluster handlers
    # Note 45: concurrently. return_exceptions=True means a crash in one cluster handler
    # Note 46: is returned as an exception object rather than re-raised, so remaining
    # Note 47: cluster results are still collected and returned to the caller.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[PodHealthOutput] = []
    # Note 48: strict=True on zip() catches any mismatch between task count and result count,
    # Note 49: which would indicate an internal bug rather than a cluster-level failure.
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="get_pod_health", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs
