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
from platform_mcp_server.validation import validate_node_pool

log = structlog.get_logger()


def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):  # fmt: skip
        return None


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

    # Pair NodeUpgrade → NodeReady per node to get per-node durations
    upgrade_times: dict[str, datetime] = {}
    ready_times: dict[str, datetime] = {}
    for evt in node_events:
        node_name = evt.get("node_name", "")
        ts = _parse_ts(evt.get("timestamp"))
        if not ts:
            continue
        if evt["reason"] == "NodeUpgrade":
            if node_name not in upgrade_times or ts < upgrade_times[node_name]:
                upgrade_times[node_name] = ts
        elif evt["reason"] == "NodeReady" and (node_name not in ready_times or ts > ready_times[node_name]):
            ready_times[node_name] = ts

    # Calculate per-node durations for completed nodes
    completed_durations: dict[str, float] = {}
    for node_name, start_ts in upgrade_times.items():
        end_ts = ready_times.get(node_name)
        if end_ts and end_ts > start_ts:
            completed_durations[node_name] = (end_ts - start_ts).total_seconds()

    current_run: CurrentRunMetrics | None = None
    if completed_durations:
        durations = list(completed_durations.values())
        mean_per_node = sum(durations) / len(durations)
        nodes_in_progress = len(upgrade_times) - len(completed_durations)
        estimated_remaining = mean_per_node * nodes_in_progress if nodes_in_progress > 0 else None

        # Wall-clock elapsed from earliest NodeUpgrade event to now
        earliest_start = min(upgrade_times.values())
        wall_clock_elapsed = (datetime.now(tz=UTC) - earliest_start).total_seconds()

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
        p90_idx = int(len(all_durations) * 0.9)
        p90_dur = all_durations[min(p90_idx, len(all_durations) - 1)]
        baseline_seconds = thresholds.upgrade_anomaly_minutes * 60
        all_within = all(d <= baseline_seconds for d in all_durations)

        stats = HistoricalStats(
            mean_duration_seconds=mean_dur,
            p90_duration_seconds=p90_dur,
            all_within_baseline=all_within,
        )

    # Anomaly flag
    anomaly_flag: str | None = None
    if current_run:
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
    tasks = [get_upgrade_metrics_handler(cid, node_pool, history_count) for cid in ALL_CLUSTER_IDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[UpgradeDurationOutput] = []
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="get_upgrade_duration_metrics", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs
