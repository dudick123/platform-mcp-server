"""get_upgrade_duration_metrics — elapsed time, estimated remaining, historical baselines."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.clients.k8s_events import K8sEventsClient
from platform_mcp_server.config import ALL_CLUSTER_IDS, get_thresholds, resolve_cluster
from platform_mcp_server.models import (
    CurrentRunMetrics,
    HistoricalStats,
    HistoricalUpgradeRecord,
    ToolError,
    UpgradeDurationOutput,
)
from platform_mcp_server.utils import parse_iso_timestamp
from platform_mcp_server.validation import validate_node_pool

log = structlog.get_logger()


_parse_ts = parse_iso_timestamp


async def get_upgrade_metrics_handler(
    cluster_id: str,
    node_pool: str,
    history_count: int = 5,
) -> UpgradeDurationOutput:
    """Core handler for get_upgrade_duration_metrics on a single cluster."""
    validate_node_pool(node_pool)
    config = resolve_cluster(cluster_id)
    events_client = K8sEventsClient(config)
    aks_client = AzureAksClient(config)
    thresholds = get_thresholds()
    errors: list[ToolError] = []

    # Get current run events
    node_events = await events_client.get_node_events(reasons=["NodeUpgrade", "NodeReady"])

    # Note 9: Two separate dicts track the earliest NodeUpgrade and latest
    # Note 10: NodeReady timestamp per node. Using dicts keyed by node name
    # Note 11: makes the subsequent pairing O(1) per lookup rather than O(n).
    # Pair NodeUpgrade → NodeReady per node to get per-node durations
    upgrade_times: dict[str, datetime] = {}
    ready_times: dict[str, datetime] = {}
    for evt in node_events:
        node_name = evt.get("node_name", "")
        ts = _parse_ts(evt.get("timestamp"))
        if not ts:
            continue
        # Note 12: For NodeUpgrade we keep the EARLIEST timestamp because a node
        # Note 13: may emit multiple upgrade events; the first one marks when
        # Note 14: Kubernetes actually began draining the node.
        if evt["reason"] == "NodeUpgrade":
            if node_name not in upgrade_times or ts < upgrade_times[node_name]:
                upgrade_times[node_name] = ts
        # Note 15: For NodeReady we keep the LATEST timestamp -- after a reboot
        # Note 16: kubelet can fire several NodeReady events as conditions stabilise;
        # Note 17: the last one is when the node was truly healthy and rejoined scheduling.
        elif evt["reason"] == "NodeReady" and (node_name not in ready_times or ts > ready_times[node_name]):
            ready_times[node_name] = ts

    # Calculate per-node durations for completed nodes
    completed_durations: dict[str, float] = {}
    for node_name, start_ts in upgrade_times.items():
        end_ts = ready_times.get(node_name)
        # Note 18: The guard `end_ts > start_ts` filters out event ordering
        # Note 19: anomalies where a stale NodeReady precedes the upgrade event,
        # Note 20: which would produce a negative (nonsensical) duration.
        if end_ts and end_ts > start_ts:
            completed_durations[node_name] = (end_ts - start_ts).total_seconds()

    current_run: CurrentRunMetrics | None = None
    if completed_durations:
        durations = list(completed_durations.values())
        mean_per_node = sum(durations) / len(durations)
        nodes_in_progress = len(upgrade_times) - len(completed_durations)
        # Note 21: estimated_remaining is None when all nodes are already done;
        # Note 22: multiplying by zero would be misleading because the upgrade
        # Note 23: is complete, not estimated to take zero seconds.
        estimated_remaining = mean_per_node * nodes_in_progress if nodes_in_progress > 0 else None

        # Wall-clock elapsed from earliest NodeUpgrade event to now
        # Note 24: min(upgrade_times.values()) finds the earliest start across ALL
        # Note 25: nodes, giving the true wall-clock start of the overall upgrade.
        # Note 26: Wall-clock elapsed differs from mean per-node: it measures the
        # Note 27: real time a human operator has been waiting (including any overlap
        # Note 28: of nodes upgrading in parallel), while mean per-node measures the
        # Note 29: average individual node cost and drives the remaining estimate.
        earliest_start = min(upgrade_times.values())
        wall_clock_elapsed = (datetime.now(tz=UTC) - earliest_start).total_seconds()

        # Note 30: sorted() on (name, duration) tuples sorts by duration (index 1)
        # Note 31: ascending, so index [0] is the fastest node and index [-1] is the
        # Note 32: slowest; negative indexing is idiomatic Python for the last item.
        sorted_nodes = sorted(completed_durations.items(), key=lambda x: x[1])
        fastest = sorted_nodes[0][0] if sorted_nodes else None
        slowest = sorted_nodes[-1][0] if sorted_nodes else None

        current_run = CurrentRunMetrics(
            elapsed_seconds=wall_clock_elapsed,
            estimated_remaining_seconds=estimated_remaining,
            nodes_completed=len(completed_durations),
            nodes_total=len(upgrade_times),
            mean_seconds_per_node=mean_per_node,
            slowest_node=slowest,
            fastest_node=fastest,
        )

    # Get historical data from Activity Log
    try:
        activity_records = await aks_client.get_activity_log_upgrades(count=history_count)
    except Exception:
        activity_records = []
        errors.append(
            ToolError(
                error="Failed to retrieve historical upgrade data",
                source="activity-log",
                cluster=cluster_id,
                partial_data=True,
            )
        )

    historical: list[HistoricalUpgradeRecord] = []
    for record in activity_records:
        duration = record.get("duration_seconds")
        if duration is not None:
            historical.append(
                HistoricalUpgradeRecord(
                    date=record.get("date", "unknown"),
                    version_path=record.get("description", "unknown"),
                    total_duration_seconds=duration,
                    node_count=0,  # Activity log doesn't give per-node detail
                    min_per_node_seconds=0,
                    max_per_node_seconds=0,
                )
            )

    # Statistical summary
    stats: HistoricalStats | None = None
    if historical:
        all_durations = [h.total_duration_seconds for h in historical]
        all_durations.sort()
        mean_dur = sum(all_durations) / len(all_durations)
        # Note 33: P90 index is computed as int(len * 0.9), which is a floor
        # Note 34: division into the sorted list. For example, with 10 items the
        # Note 35: index is 9 (the last element), meaning 90% of values are at or
        # Note 36: below that point. The min(..., len - 1) clamp prevents an off-
        # Note 37: by-one IndexError when the list is very short (e.g. 1 element).
        p90_idx = int(len(all_durations) * 0.9)
        p90_dur = all_durations[min(p90_idx, len(all_durations) - 1)]
        # Note 38: The threshold is stored in minutes (human-readable config) but
        # Note 39: durations are in seconds, so * 60 converts to the same unit
        # Note 40: before the comparison.
        baseline_seconds = thresholds.upgrade_anomaly_minutes * 60
        # Note 41: all_within_baseline is True only when EVERY historical duration
        # Note 42: is under the threshold -- a single outlier flips it to False.
        # Note 43: This is stricter than a "usually within baseline" check, giving
        # Note 44: the operator a clear signal that the cluster has been consistent.
        all_within = all(d <= baseline_seconds for d in all_durations)

        stats = HistoricalStats(
            mean_duration_seconds=mean_dur,
            p90_duration_seconds=p90_dur,
            all_within_baseline=all_within,
        )

    # Anomaly flag
    anomaly_flag: str | None = None
    if current_run:
        # Note 45: estimated_total projects the final upgrade cost by adding the
        # Note 46: already-elapsed seconds to the remaining estimate. This means the
        # Note 47: flag can fire before the upgrade finishes -- early warning is more
        # Note 48: useful than a post-mortem alert. The formula is:
        # Note 49:   estimated_total = elapsed + estimated_remaining
        # Estimate total duration
        estimated_total = current_run.elapsed_seconds
        if current_run.estimated_remaining_seconds:
            estimated_total += current_run.estimated_remaining_seconds
        baseline_minutes = thresholds.upgrade_anomaly_minutes
        if estimated_total > baseline_minutes * 60:
            anomaly_flag = (
                f"Estimated duration ({int(estimated_total / 60)}m) exceeds the "
                f"{baseline_minutes}-minute expected baseline for ADO pipeline upgrades"
            )

    # Summary
    parts: list[str] = []
    if current_run:
        mean_s = current_run.mean_seconds_per_node
        parts.append(f"Current run: {current_run.nodes_completed} nodes completed, {mean_s:.0f}s mean per node")
    else:
        parts.append("No active upgrade")
    if historical:
        found = len(historical)
        if found < history_count:
            parts.append(f"{found} of {history_count} requested historical records found")
        else:
            parts.append(f"{found} historical records")
    else:
        parts.append("no historical data")

    return UpgradeDurationOutput(
        cluster=cluster_id,
        node_pool=node_pool,
        current_run=current_run,
        historical=historical,
        stats=stats,
        anomaly_flag=anomaly_flag,
        summary="; ".join(parts),
        timestamp=datetime.now(tz=UTC).isoformat(),
        errors=errors,
    )


async def get_upgrade_metrics_all(
    node_pool: str,
    history_count: int = 5,
) -> list[UpgradeDurationOutput]:
    """Fan-out get_upgrade_duration_metrics to all clusters concurrently."""
    # Note 50: asyncio.gather launches all per-cluster coroutines concurrently so
    # Note 51: network latency for N clusters is paid once in parallel rather than
    # Note 52: N times sequentially. return_exceptions=True prevents one failing
    # Note 53: cluster from cancelling the rest; failures are handled in the loop.
    tasks = [get_upgrade_metrics_handler(cid, node_pool, history_count) for cid in ALL_CLUSTER_IDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[UpgradeDurationOutput] = []
    # Note 54: zip(..., strict=True) enforces that ALL_CLUSTER_IDS and results have
    # Note 55: the same length at runtime; a mismatch would indicate a programming
    # Note 56: error and raises ValueError immediately rather than silently dropping
    # Note 57: items, which would produce misleading output.
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="get_upgrade_duration_metrics", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs
