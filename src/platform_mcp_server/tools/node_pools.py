"""check_node_pool_pressure — CPU/memory request ratios and pressure levels per node pool."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

from platform_mcp_server.clients.k8s_core import K8sCoreClient
from platform_mcp_server.clients.k8s_metrics import K8sMetricsClient
from platform_mcp_server.config import ALL_CLUSTER_IDS, ThresholdConfig, get_thresholds, resolve_cluster
from platform_mcp_server.models import NodePoolPressureOutput, NodePoolResult, ToolError

log = structlog.get_logger()


# Note 1: Kubernetes CPU values use two distinct formats:
# Note 2:   "4"  means 4 whole cores, which equals 4000 millicores (m).
# Note 3:   "500m" means 500 millicores, i.e., half a core.
# Note 4: Converting everything to millicores gives a common integer-friendly unit
# Note 5: that avoids floating-point comparisons across mixed formats.
def _parse_cpu_millicores(value: str) -> float:
    """Parse a Kubernetes CPU value to millicores."""
    try:
        if value.endswith("m"):
            return float(value[:-1])
        # Note 6: A bare numeric string like "4" represents whole cores;
        # Note 7: multiply by 1000 to normalize to millicores for uniform arithmetic.
        return float(value) * 1000
    except ValueError, TypeError:
        log.warning("cpu_parse_failed", value=value)
        return 0.0


# Note 8: Kubernetes memory uses two families of suffixes with different base multipliers:
# Note 9:   Binary (IEC): Ki=1024, Mi=1024^2, Gi=1024^3 -- powers of 2.
# Note 10:  Decimal (SI):  k=1000, M=1,000,000, G=1,000,000,000 -- powers of 10.
# Note 11: The Ki vs k distinction matters: 1Ki=1024 bytes but 1k=1000 bytes.
# Note 12: Kubernetes internally stores memory in bytes, so all suffixes are normalized here.
def _parse_memory_bytes(value: str) -> float:
    """Parse a Kubernetes memory value to bytes."""
    try:
        # Note 13: Binary suffixes are checked first because they are the dominant format
        # Note 14: in Kubernetes node/pod specs. The dict maps suffix -> multiplier cleanly.
        units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
        for suffix, multiplier in units.items():
            if value.endswith(suffix):
                return float(value[: -len(suffix)]) * multiplier
        # Note 15: Decimal suffixes (k, M, G) are less common in Kubernetes but appear
        # Note 16: in some tooling output and must be handled with SI (base-10) multipliers.
        if value.endswith("k"):
            return float(value[:-1]) * 1000
        if value.endswith("M"):
            return float(value[:-1]) * 1_000_000
        if value.endswith("G"):
            return float(value[:-1]) * 1_000_000_000
        # Note 17: A bare number with no suffix is interpreted as raw bytes.
        return float(value)
    except ValueError, TypeError:
        log.warning("memory_parse_failed", value=value)
        return 0.0


def _classify_pressure(
    cpu_pct: float | None,
    mem_pct: float | None,
    pending_pods: int,
    thresholds: ThresholdConfig,
) -> Literal["ok", "warning", "critical"]:
    """Classify pressure level — highest severity wins."""
    PressureLevel = Literal["ok", "warning", "critical"]
    # Note 18: The list starts with "ok" so there is always at least one element,
    # Note 19: guaranteeing that max() never raises ValueError on an empty sequence.
    levels: list[PressureLevel] = ["ok"]

    if cpu_pct is not None:
        if cpu_pct >= thresholds.cpu_critical:
            levels.append("critical")
        elif cpu_pct >= thresholds.cpu_warning:
            levels.append("warning")

    if mem_pct is not None:
        if mem_pct >= thresholds.memory_critical:
            levels.append("critical")
        elif mem_pct >= thresholds.memory_warning:
            levels.append("warning")

    if pending_pods > thresholds.pending_pods_critical:
        levels.append("critical")
    elif pending_pods >= thresholds.pending_pods_warning:
        levels.append("warning")

    # Note 20: A numeric severity dict converts string labels to comparable integers,
    # Note 21: allowing max() with a key function to select the highest severity level.
    # Note 22: This avoids chained if/elif logic and naturally extends if new levels are added.
    severity = {"critical": 2, "warning": 1, "ok": 0}
    return max(levels, key=lambda x: severity[x])


async def check_node_pool_pressure_handler(cluster_id: str) -> NodePoolPressureOutput:
    """Core handler for check_node_pool_pressure on a single cluster."""
    config = resolve_cluster(cluster_id)
    thresholds = get_thresholds()
    core_client = K8sCoreClient(config)
    metrics_client = K8sMetricsClient(config)

    nodes = await core_client.get_nodes()
    # Note 23: The field_selector filters server-side so only Pending pods are transferred
    # Note 24: over the network, reducing payload size compared to filtering client-side.
    pods = await core_client.get_pods(field_selector="status.phase=Pending")

    # Try to get metrics; graceful degradation if unavailable
    errors: list[ToolError] = []
    metrics_by_node: dict[str, dict[str, Any]] = {}
    try:
        metrics = await metrics_client.get_node_metrics()
        for m in metrics:
            metrics_by_node[m["name"]] = m
    except Exception:
        errors.append(
            ToolError(
                error="Metrics API unavailable; utilization data omitted",
                source="metrics-server",
                cluster=cluster_id,
                partial_data=True,
            )
        )

    # Group nodes by pool
    # Note 25: defaultdict(list) eliminates the "check key exists, then append" boilerplate;
    # Note 26: accessing a missing key automatically inserts an empty list as the default.
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # Note 27: node_to_pool is a reverse-lookup dict built in O(n) once so that later
    # Note 28: pod-to-pool mapping runs in O(1) per pod instead of O(n) per pod.
    node_to_pool: dict[str, str] = {}
    for node in nodes:
        pool_name = node["pool"] or "unknown"
        pools[pool_name].append(node)
        node_to_pool[node["name"]] = pool_name

    # Count pending pods per pool (by node assignment) and unassigned
    # Note 29: defaultdict(int) initializes missing pool keys to 0 automatically,
    # Note 30: so the "+= 1" increment works without an explicit key existence check.
    pending_per_pool: dict[str, int] = defaultdict(int)
    # Note 31: "unassigned_pending" counts pods whose node_name is absent or not in
    # Note 32: node_to_pool -- these are pods the scheduler has not yet placed on any node.
    # Note 33: A pod can be Pending with a node_name when it is scheduled but not yet running;
    # Note 34: without a node_name the pod is truly unassigned (scheduler has not acted yet).
    unassigned_pending = 0
    for pod in pods:
        pod_node = pod.get("node_name")
        if pod_node and pod_node in node_to_pool:
            pending_per_pool[node_to_pool[pod_node]] += 1
        else:
            unassigned_pending += 1

    # Build results per pool
    # Note 35: sorted(pools.items()) produces deterministic output order regardless of
    # Note 36: dict insertion order, which makes LLM responses and test assertions stable.
    pool_results: list[NodePoolResult] = []
    for pool_name, pool_nodes in sorted(pools.items()):
        total_cpu_alloc = 0.0
        total_mem_alloc = 0.0
        total_cpu_usage = 0.0
        total_mem_usage = 0.0
        has_metrics = False

        ready_count = sum(1 for n in pool_nodes if n["conditions"].get("Ready") == "True")

        for node in pool_nodes:
            total_cpu_alloc += _parse_cpu_millicores(node["allocatable_cpu"])
            total_mem_alloc += _parse_memory_bytes(node["allocatable_memory"])

            node_metric = metrics_by_node.get(node["name"])
            if node_metric:
                has_metrics = True
                total_cpu_usage += _parse_cpu_millicores(node_metric["cpu_usage"])
                total_mem_usage += _parse_memory_bytes(node_metric["memory_usage"])

        # Note 37: cpu_pct is None when the metrics server is unavailable (has_metrics=False)
        # Note 38: or when allocatable CPU is zero, preventing a division-by-zero error.
        cpu_pct = (total_cpu_usage / total_cpu_alloc * 100) if has_metrics and total_cpu_alloc > 0 else None
        mem_pct = (total_mem_usage / total_mem_alloc * 100) if has_metrics and total_mem_alloc > 0 else None

        # Note 39: Unassigned pending pods are attributed to every pool because the scheduler
        # Note 40: has not yet decided which pool they will land on; this is a conservative
        # Note 41: choice that avoids under-reporting pressure on any individual pool.
        pool_pending = pending_per_pool.get(pool_name, 0) + unassigned_pending
        pressure = _classify_pressure(cpu_pct, mem_pct, pool_pending, thresholds)

        pool_results.append(
            NodePoolResult(
                pool_name=pool_name,
                cpu_requests_percent=round(cpu_pct, 1) if cpu_pct is not None else None,
                memory_requests_percent=round(mem_pct, 1) if mem_pct is not None else None,
                pending_pods=pool_pending,
                ready_nodes=ready_count,
                max_nodes=None,  # Can be enriched from AKS API later
                pressure_level=pressure,
            )
        )

    under_pressure = sum(1 for p in pool_results if p.pressure_level != "ok")
    total_pools = len(pool_results)
    if under_pressure > 0:
        summary = f"{under_pressure} of {total_pools} node pools in {cluster_id} under pressure"
    else:
        summary = f"All {total_pools} node pools in {cluster_id} are healthy"

    return NodePoolPressureOutput(
        cluster=cluster_id,
        pools=pool_results,
        summary=summary,
        timestamp=datetime.now(tz=UTC).isoformat(),
        errors=errors,
    )


# Note 42: asyncio.gather(*tasks, return_exceptions=True) runs all cluster checks
# Note 43: concurrently in a single event-loop turn (fan-out pattern). Without
# Note 44: return_exceptions=True, the first failing cluster would cancel all others
# Note 45: via exception propagation; with it, each result is either a value or an exception
# Note 46: object that can be inspected per-cluster without aborting the whole fleet check.
async def check_node_pool_pressure_all() -> list[NodePoolPressureOutput]:
    """Fan-out check_node_pool_pressure to all clusters concurrently."""
    tasks = [check_node_pool_pressure_handler(cid) for cid in ALL_CLUSTER_IDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[NodePoolPressureOutput] = []
    # Note 47: zip(..., strict=True) raises ValueError if ALL_CLUSTER_IDS and results
    # Note 48: have different lengths. This would indicate a bug in gather() result alignment
    # Note 49: and is safer than silently dropping trailing elements as plain zip() would.
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="check_node_pool_pressure", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs
