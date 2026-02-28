"""get_upgrade_progress — per-node upgrade state during in-flight upgrades."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.clients.k8s_core import K8sCoreClient
from platform_mcp_server.clients.k8s_events import K8sEventsClient
from platform_mcp_server.clients.k8s_policy import K8sPolicyClient
from platform_mcp_server.config import ALL_CLUSTER_IDS, get_thresholds, resolve_cluster
from platform_mcp_server.models import (
    AffectedPod,
    NodeUpgradeState,
    PodTransitionSummary,
    ToolError,
    UpgradeProgressOutput,
)
from platform_mcp_server.tools.pod_classification import categorize_failure, is_unhealthy
from platform_mcp_server.validation import validate_node_pool

log = structlog.get_logger()

# Note 1: _POD_TRANSITION_CAP limits the list of individual affected pods returned
# Note 2: to the caller. 20 is intentionally smaller than the cap used in pod_health
# Note 3: because this summary is supplementary context during an upgrade -- the
# Note 4: operator primarily cares about node states, not an exhaustive pod list.
_POD_TRANSITION_CAP = 20
_ACTIVE_UPGRADE_STATES = {"cordoned", "upgrading", "pdb_blocked", "stalled"}


# Note 5: _parse_event_timestamp duplicates the _parse_ts pattern from
# Note 6: upgrade_metrics.py intentionally. Both modules need the same
# Note 7: safe ISO 8601 parsing, but they live in separate files to keep
# Note 8: each module self-contained and independently importable without
# Note 9: creating a circular dependency through a shared utility module.
def _parse_event_timestamp(ts_str: str | None) -> datetime | None:
    """Parse an ISO timestamp string to a datetime."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):  # fmt: skip
        return None


# Note 10: The return type uses Literal to enumerate all six valid states as
# Note 11: strings. This lets type checkers (mypy/pyright) catch any call site
# Note 12: that compares against a state string not in the allowed set.
def _classify_node_state(
    node: dict[str, Any],
    target_version: str,
    node_events: dict[str, list[dict[str, Any]]],
    pdb_blockers: set[str],
    upgrade_start: datetime | None,
    thresholds_minutes: int,
) -> Literal["upgraded", "upgrading", "cordoned", "pdb_blocked", "pending", "stalled"]:
    """Classify a node into one of the six upgrade states."""
    name = node["name"]
    version = node.get("version", "").lstrip("v")
    unschedulable = node.get("unschedulable", False)

    events = node_events.get(name, [])
    has_upgrade_event = any(e["reason"] == "NodeUpgrade" for e in events)
    has_ready_event = any(e["reason"] == "NodeReady" for e in events)

    # Note 13: The "upgraded" check comes first because it is the terminal state.
    # Note 14: A node that has both events and the right version is definitively done;
    # Note 15: checking it first avoids accidentally classifying it as "upgrading".
    # Upgraded: has NodeReady after NodeUpgrade and version matches target
    if has_upgrade_event and has_ready_event and version == target_version:
        return "upgraded"

    # Note 16: The "upgrading" branch is entered only when NodeUpgrade fired but
    # Note 17: NodeReady has not yet, meaning the node is mid-process. Within that
    # Note 18: branch, stall and PDB checks come before the plain "upgrading" return
    # Note 19: because they are more specific conditions that need to surface first.
    # Upgrading: has NodeUpgrade event but not yet NodeReady
    if has_upgrade_event and not has_ready_event:
        # Check if stalled first
        if upgrade_start:
            # Note 20: thresholds_minutes is stored in minutes (human-readable config);
            # Note 21: dividing total_seconds() by 60 converts to the same unit for
            # Note 22: the comparison. The threshold is the upgrade_anomaly_minutes value.
            elapsed_minutes = (datetime.now(tz=UTC) - upgrade_start).total_seconds() / 60
            if elapsed_minutes > thresholds_minutes:
                # Note 23: pdb_blockers is a set of PDB names, giving O(1) membership
                # Note 24: tests. When the upgrade has exceeded the time threshold AND
                # Note 25: a PDB is blocking AND the node is cordoned, the delay is
                # Note 26: informational (expected PDB behavior) rather than a true stall.
                if pdb_blockers and unschedulable:
                    return "pdb_blocked"
                return "stalled"
        # PDB blocked: actively upgrading, cordoned, and PDB blocking drain
        if unschedulable and pdb_blockers:
            return "pdb_blocked"
        return "upgrading"

    # Note 27: "cordoned" is checked after the upgrading branch because a node with
    # Note 28: a NodeUpgrade event is always at least "upgrading", not merely "cordoned".
    # Note 29: Reaching this branch means the node has no NodeUpgrade event, so
    # Note 30: being unschedulable indicates it was cordoned in preparation but the
    # Note 31: upgrade event has not fired yet.
    # Cordoned: unschedulable but no NodeUpgrade event yet
    if unschedulable:
        return "cordoned"

    # Pending: old version, not yet cordoned
    return "pending"


async def _collect_pod_transitions(
    core_client: K8sCoreClient,
    node_states: list[NodeUpgradeState],
    errors: list[ToolError],
    cluster_id: str,
) -> PodTransitionSummary:
    """Collect pod transitions on nodes actively involved in the upgrade."""
    # Note 32: active_node_names uses a set comprehension so membership tests
    # Note 33: against it later are O(1) instead of O(n) for a list.
    # Identify nodes in active upgrade states
    active_node_names = {n.name for n in node_states if n.state in _ACTIVE_UPGRADE_STATES}

    # Note 34: The early return here is a short-circuit guard: if no node is in
    # Note 35: an active upgrade state there is nothing to report, and the
    # Note 36: expensive pod-list API call should be skipped entirely. Returning
    # Note 37: an empty PodTransitionSummary() keeps the return type consistent.
    if not active_node_names:
        return PodTransitionSummary()

    try:
        all_pods = await core_client.get_pods()
    except Exception:
        errors.append(
            ToolError(
                error="Failed to retrieve pods for transition summary",
                source="k8s-api",
                cluster=cluster_id,
                partial_data=True,
            )
        )
        return PodTransitionSummary()

    # Filter to unhealthy pods on active upgrade nodes
    affected = [p for p in all_pods if p.get("node_name") in active_node_names and is_unhealthy(p)]

    pending_count = sum(1 for p in affected if p.get("phase") == "Pending")
    failed_count = sum(1 for p in affected if p.get("phase") in ("Failed", "Unknown"))
    # Include running pods with bad container states in failed count
    other_unhealthy = len(affected) - pending_count - failed_count
    failed_count += other_unhealthy

    # Group by failure category
    by_category: dict[str, int] = {}
    for pod in affected:
        category = categorize_failure(pod.get("reason"), pod.get("container_statuses", []))
        by_category[category] = by_category.get(category, 0) + 1

    # Note 38: phase_order maps phase strings to integers so the sort key is
    # Note 39: a cheap integer comparison rather than a string comparison.
    # Note 40: Failed pods sort first (0) because they are the highest-severity
    # Note 41: signal during an upgrade; Pending pods (2) are expected churn.
    # Note 42: Phases not in the dict get a default of 3 and sort last, keeping
    # Note 43: them out of the way without requiring an exhaustive mapping.
    # Sort: Failed first, then Pending
    phase_order = {"Failed": 0, "Unknown": 1, "Pending": 2}
    affected.sort(key=lambda p: phase_order.get(p.get("phase", ""), 3))

    # Build affected pod list (capped)
    affected_pods = [
        AffectedPod(
            name=p["name"],
            namespace=p.get("namespace", "unknown"),
            phase=p.get("phase", "Unknown"),
            reason=p.get("reason"),
            node_name=p.get("node_name"),
        )
        for p in affected[:_POD_TRANSITION_CAP]
    ]

    return PodTransitionSummary(
        pending_count=pending_count,
        failed_count=failed_count,
        by_category=by_category,
        affected_pods=affected_pods,
        total_affected=len(affected),
    )


async def get_upgrade_progress_handler(
    cluster_id: str,
    node_pool: str | None = None,
) -> UpgradeProgressOutput:
    """Core handler for get_upgrade_progress on a single cluster."""
    validate_node_pool(node_pool)
    config = resolve_cluster(cluster_id)
    aks_client = AzureAksClient(config)
    core_client = K8sCoreClient(config)
    events_client = K8sEventsClient(config)
    policy_client = K8sPolicyClient(config)
    thresholds = get_thresholds()
    errors: list[ToolError] = []

    cluster_info = await aks_client.get_cluster_info()

    # Check if any pool is upgrading
    upgrading_pools = [
        p
        for p in cluster_info.get("node_pools", [])
        if p.get("provisioning_state") == "Upgrading" or p.get("current_version") != p.get("target_version")
    ]

    if node_pool:
        upgrading_pools = [p for p in upgrading_pools if p["name"] == node_pool]

    if not upgrading_pools:
        return UpgradeProgressOutput(
            cluster=cluster_id,
            upgrade_in_progress=False,
            nodes=[],
            summary=f"No upgrade in progress for {cluster_id}",
            timestamp=datetime.now(tz=UTC).isoformat(),
            errors=errors,
        )

    target_pool = upgrading_pools[0]
    target_version = target_pool.get("target_version", "unknown")

    # Get nodes and events
    nodes = await core_client.get_nodes()
    if node_pool:
        nodes = [n for n in nodes if n.get("pool") == node_pool]

    node_events_list = await events_client.get_node_events(reasons=["NodeUpgrade", "NodeReady", "NodeNotReady"])

    # Group events by node
    node_events: dict[str, list[dict[str, Any]]] = {}
    for evt in node_events_list:
        node_name = evt.get("node_name", "")
        if node_name not in node_events:
            node_events[node_name] = []
        node_events[node_name].append(evt)

    # Get PDB blockers
    pdbs = await policy_client.get_pdbs()
    blocker_list = await policy_client.evaluate_pdb_satisfiability(pdbs)
    # Note 44: pdb_blocker_names is a set so that _classify_node_state can test
    # Note 45: membership in O(1). Converting from the list here, once, avoids
    # Note 46: repeating the conversion inside the per-node classification loop.
    pdb_blocker_names = {b["name"] for b in blocker_list}

    # Note 47: upgrade_start is the EARLIEST NodeUpgrade event across ALL nodes,
    # Note 48: not per-node. This single timestamp represents when the overall
    # Note 49: upgrade wave began and is used to compute total elapsed time for
    # Note 50: the anomaly threshold check, independent of individual node timing.
    # Find upgrade start time from earliest NodeUpgrade event
    upgrade_start: datetime | None = None
    for events_for_node in node_events.values():
        for evt in events_for_node:
            if evt["reason"] == "NodeUpgrade":
                ts = _parse_event_timestamp(evt.get("timestamp"))
                if ts and (upgrade_start is None or ts < upgrade_start):
                    upgrade_start = ts

    # Classify each node
    node_states: list[NodeUpgradeState] = []
    for node in nodes:
        state = _classify_node_state(
            node, target_version, node_events, pdb_blocker_names, upgrade_start, thresholds.upgrade_anomaly_minutes
        )

        blocking_pdb = None
        blocking_pdb_ns = None
        if state == "pdb_blocked" and blocker_list:
            blocking_pdb = blocker_list[0]["name"]
            blocking_pdb_ns = blocker_list[0].get("namespace")

        node_states.append(
            NodeUpgradeState(
                name=node["name"],
                state=state,
                version=node.get("version", "unknown"),
                blocking_pdb=blocking_pdb,
                blocking_pdb_namespace=blocking_pdb_ns,
            )
        )

    # Compute stats
    upgraded_count = sum(1 for n in node_states if n.state == "upgraded")
    total_count = len(node_states)
    remaining = total_count - upgraded_count

    # Duration estimation
    elapsed_seconds: float | None = None
    estimated_remaining: float | None = None
    if upgrade_start:
        elapsed_seconds = (datetime.now(tz=UTC) - upgrade_start).total_seconds()
        if upgraded_count > 0 and remaining > 0:
            mean_per_node = elapsed_seconds / upgraded_count
            # Note 51: estimated_remaining uses linear extrapolation: mean time per
            # Note 52: completed node multiplied by the number still remaining. This
            # Note 53: assumes nodes upgrade at a roughly uniform rate, which is a
            # Note 54: reasonable approximation for homogeneous node pools. Formula:
            # Note 55:   estimated_remaining = mean_per_node * remaining
            estimated_remaining = mean_per_node * remaining

    # Note 56: The anomaly flag has two distinct cases: a PDB block is an expected
    # Note 57: (informational) delay caused by pod disruption budgets preventing drain,
    # Note 58: while a plain stall with no PDB explanation is a genuine problem.
    # Note 59: Separating the two cases lets operators distinguish "waiting on PDB"
    # Note 60: from "something is actually broken", avoiding false alarm escalations.
    # Note 61: The comparison `elapsed_seconds > thresholds.upgrade_anomaly_minutes * 60`
    # Note 62: converts the minute-based config threshold to seconds before comparing.
    # Anomaly flagging
    anomaly_flag: str | None = None
    if elapsed_seconds and elapsed_seconds > thresholds.upgrade_anomaly_minutes * 60:
        has_pdb_block = any(n.state == "pdb_blocked" for n in node_states)
        if has_pdb_block:
            anomaly_flag = f"Upgrade duration ({int(elapsed_seconds / 60)}m) exceeds baseline but PDB block detected"
        else:
            anomaly_flag = (
                f"Upgrade duration ({int(elapsed_seconds / 60)}m) exceeds the "
                f"{thresholds.upgrade_anomaly_minutes}-minute expected baseline"
            )

    # Note 63: _collect_pod_transitions is called only after node_states is built
    # Note 64: because it needs the full list to determine which nodes are active.
    # Note 65: The function itself short-circuits immediately if no active nodes
    # Note 66: exist, so there is no wasted async call in the quiescent case.
    # Pod transition summary
    pod_transitions = await _collect_pod_transitions(core_client, node_states, errors, cluster_id)

    summary = (
        f"{cluster_id}: {upgraded_count}/{total_count} nodes upgraded"
        f"{', upgrade in progress' if remaining > 0 else ', upgrade complete'}"
    )

    return UpgradeProgressOutput(
        cluster=cluster_id,
        upgrade_in_progress=True,
        node_pool=target_pool["name"],
        target_version=target_version,
        nodes=node_states,
        nodes_total=total_count,
        nodes_upgraded=upgraded_count,
        nodes_remaining=remaining,
        elapsed_seconds=elapsed_seconds,
        estimated_remaining_seconds=estimated_remaining,
        anomaly_flag=anomaly_flag,
        pod_transitions=pod_transitions,
        summary=summary,
        timestamp=datetime.now(tz=UTC).isoformat(),
        errors=errors,
    )


async def get_upgrade_progress_all(node_pool: str | None = None) -> list[UpgradeProgressOutput]:
    """Fan-out get_upgrade_progress to all clusters concurrently."""
    # Note 67: asyncio.gather fires all per-cluster coroutines concurrently,
    # Note 68: so the total latency is roughly the slowest cluster rather than
    # Note 69: the sum of all cluster latencies. return_exceptions=True means
    # Note 70: a single cluster failure does not cancel the remaining tasks.
    tasks = [get_upgrade_progress_handler(cid, node_pool) for cid in ALL_CLUSTER_IDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[UpgradeProgressOutput] = []
    # Note 71: strict=True in zip is a correctness guard -- it raises ValueError
    # Note 72: if ALL_CLUSTER_IDS and results have different lengths. Since
    # Note 73: asyncio.gather always returns exactly one result per task, this
    # Note 74: would only fail if ALL_CLUSTER_IDS was mutated during execution,
    # Note 75: making strict=True an inexpensive sanity check worth keeping.
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="get_upgrade_progress", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs
