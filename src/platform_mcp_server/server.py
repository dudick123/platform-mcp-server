"""MCP server entry point and tool registration."""

from __future__ import annotations

import sys
import time

import structlog
from mcp.server.fastmcp import FastMCP

from platform_mcp_server.models import scrub_sensitive_values
from platform_mcp_server.tools.k8s_upgrades import get_upgrade_status_all, get_upgrade_status_handler
from platform_mcp_server.tools.node_pools import check_node_pool_pressure_all, check_node_pool_pressure_handler
from platform_mcp_server.tools.pdb_check import check_pdb_risk_all, check_pdb_risk_handler
from platform_mcp_server.tools.pod_health import get_pod_health_all, get_pod_health_handler
from platform_mcp_server.tools.upgrade_metrics import get_upgrade_metrics_all, get_upgrade_metrics_handler
from platform_mcp_server.tools.upgrade_progress import get_upgrade_progress_all, get_upgrade_progress_handler

# Configure structlog for JSON output to stderr
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

log = structlog.get_logger()

mcp = FastMCP("Platform MCP Server")


@mcp.tool()
async def check_node_pool_pressure(cluster: str) -> str:
    """Check CPU and memory pressure levels for node pools in an AKS cluster.

    Returns per-pool CPU request ratio, memory request ratio, pending pod count,
    ready node count, max node count, and a pressure level (ok/warning/critical).
    Use this when investigating node resource exhaustion or autoscaler headroom.

    Args:
        cluster: Cluster ID (e.g., 'prod-eastus') or 'all' for fleet-wide query.
    """
    start = time.monotonic()
    try:
        if cluster == "all":
            results = await check_node_pool_pressure_all()
            output = "\n\n".join(scrub_sensitive_values(r.model_dump_json(indent=2)) for r in results)
        else:
            result = await check_node_pool_pressure_handler(cluster)
            output = scrub_sensitive_values(result.model_dump_json(indent=2))
        log.info("tool_completed", tool="check_node_pool_pressure", cluster=cluster, latency_ms=_elapsed_ms(start))
        return output
    except Exception as e:
        log.error("tool_failed", tool="check_node_pool_pressure", cluster=cluster, error=str(e))
        raise


@mcp.tool()
async def get_pod_health(
    cluster: str,
    namespace: str | None = None,
    status_filter: str = "all",
    lookback_minutes: int = 30,
) -> str:
    """Get diagnostics for failed and pending pods in an AKS cluster.

    Returns pods grouped by failure category (scheduling/runtime/registry/config),
    with restart counts, event context, and OOMKill detection. Results capped at 50 pods.
    Use this when investigating pod failures, CrashLoopBackOff, or scheduling issues.

    Args:
        cluster: Cluster ID (e.g., 'prod-eastus') or 'all' for fleet-wide query.
        namespace: Filter to a specific namespace. Omit for all namespaces.
        status_filter: Filter by status: 'pending', 'failed', or 'all'.
        lookback_minutes: Include resolved failures within this window. Default 30.
    """
    start = time.monotonic()
    try:
        if cluster == "all":
            results = await get_pod_health_all(namespace, status_filter, lookback_minutes)
            output = "\n\n".join(scrub_sensitive_values(r.model_dump_json(indent=2)) for r in results)
        else:
            result = await get_pod_health_handler(cluster, namespace, status_filter, lookback_minutes)
            output = scrub_sensitive_values(result.model_dump_json(indent=2))
        log.info("tool_completed", tool="get_pod_health", cluster=cluster, latency_ms=_elapsed_ms(start))
        return output
    except Exception as e:
        log.error("tool_failed", tool="get_pod_health", cluster=cluster, error=str(e))
        raise


@mcp.tool()
async def get_kubernetes_upgrade_status(cluster: str) -> str:
    """Get Kubernetes version and upgrade status for AKS clusters.

    Returns control plane version, per-node-pool versions, available upgrades,
    support status, and deprecated version warnings. Detects active in-flight upgrades.
    Use this to check version currency, plan upgrades, or verify upgrade completion.

    Args:
        cluster: Cluster ID (e.g., 'prod-eastus') or 'all' for fleet-wide version table.
    """
    start = time.monotonic()
    try:
        if cluster == "all":
            results = await get_upgrade_status_all()
            output = "\n\n".join(scrub_sensitive_values(r.model_dump_json(indent=2)) for r in results)
        else:
            result = await get_upgrade_status_handler(cluster)
            output = scrub_sensitive_values(result.model_dump_json(indent=2))
        log.info("tool_completed", tool="get_kubernetes_upgrade_status", cluster=cluster, latency_ms=_elapsed_ms(start))
        return output
    except Exception as e:
        log.error("tool_failed", tool="get_kubernetes_upgrade_status", cluster=cluster, error=str(e))
        raise


@mcp.tool()
async def get_upgrade_progress(cluster: str, node_pool: str | None = None) -> str:
    """Track per-node progress during an in-flight AKS upgrade.

    Returns each node's state (upgraded/upgrading/cordoned/pdb_blocked/pending/stalled),
    elapsed and estimated remaining time, and anomaly flags for slow upgrades.
    Use this when monitoring an active upgrade or diagnosing upgrade stalls.

    Args:
        cluster: Cluster ID (e.g., 'prod-eastus') or 'all' for fleet-wide query.
        node_pool: Filter to a specific node pool. Omit for all upgrading pools.
    """
    start = time.monotonic()
    try:
        if cluster == "all":
            results = await get_upgrade_progress_all(node_pool)
            output = "\n\n".join(scrub_sensitive_values(r.model_dump_json(indent=2)) for r in results)
        else:
            result = await get_upgrade_progress_handler(cluster, node_pool)
            output = scrub_sensitive_values(result.model_dump_json(indent=2))
        log.info("tool_completed", tool="get_upgrade_progress", cluster=cluster, latency_ms=_elapsed_ms(start))
        return output
    except Exception as e:
        log.error("tool_failed", tool="get_upgrade_progress", cluster=cluster, error=str(e))
        raise


@mcp.tool()
async def get_upgrade_duration_metrics(cluster: str, node_pool: str, history_count: int = 5) -> str:
    """Get upgrade duration metrics including current timing and historical baselines.

    Returns current run elapsed time and per-node timing (from Events API),
    plus historical upgrade durations with mean, P90, min, and max (from Activity Log).
    Flags durations exceeding the 60-minute baseline. Use this for upgrade window sizing.

    Args:
        cluster: Cluster ID (e.g., 'prod-eastus') or 'all' for fleet-wide query.
        node_pool: The node pool to query duration metrics for.
        history_count: Number of historical records to retrieve. Default 5.
    """
    start = time.monotonic()
    try:
        if cluster == "all":
            results = await get_upgrade_metrics_all(node_pool, history_count)
            output = "\n\n".join(scrub_sensitive_values(r.model_dump_json(indent=2)) for r in results)
        else:
            result = await get_upgrade_metrics_handler(cluster, node_pool, history_count)
            output = scrub_sensitive_values(result.model_dump_json(indent=2))
        log.info("tool_completed", tool="get_upgrade_duration_metrics", cluster=cluster, latency_ms=_elapsed_ms(start))
        return output
    except Exception as e:
        log.error("tool_failed", tool="get_upgrade_duration_metrics", cluster=cluster, error=str(e))
        raise


@mcp.tool()
async def check_pdb_upgrade_risk(cluster: str, node_pool: str | None = None, mode: str = "preflight") -> str:
    """Check PodDisruptionBudget risks that could block AKS upgrades.

    In preflight mode: evaluates all PDBs for drain-block risk before an upgrade starts.
    In live mode: identifies PDBs currently blocking eviction on cordoned nodes.
    Use this before starting an upgrade (preflight) or to diagnose upgrade stalls (live).

    Args:
        cluster: Cluster ID (e.g., 'prod-eastus') or 'all' for fleet-wide query.
        node_pool: Filter to PDBs affecting pods on this pool. Omit for cluster-wide.
        mode: 'preflight' to evaluate risk before upgrade, 'live' for active block detection.
    """
    start = time.monotonic()
    try:
        if cluster == "all":
            results = await check_pdb_risk_all(node_pool, mode)
            output = "\n\n".join(scrub_sensitive_values(r.model_dump_json(indent=2)) for r in results)
        else:
            result = await check_pdb_risk_handler(cluster, node_pool, mode)
            output = scrub_sensitive_values(result.model_dump_json(indent=2))
        log.info("tool_completed", tool="check_pdb_upgrade_risk", cluster=cluster, latency_ms=_elapsed_ms(start))
        return output
    except Exception as e:
        log.error("tool_failed", tool="check_pdb_upgrade_risk", cluster=cluster, error=str(e))
        raise


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


# Claude Desktop MCP server configuration example:
# Add to ~/Library/Application Support/Claude/claude_desktop_config.json (macOS)
# or %APPDATA%\Claude\claude_desktop_config.json (Windows):
#
# {
#   "mcpServers": {
#     "platform-mcp-server": {
#       "command": "uv",
#       "args": ["run", "--directory", "/path/to/platform-mcp-server", "python", "-m", "platform_mcp_server.server"]
#     }
#   }
# }

if __name__ == "__main__":
    mcp.run(transport="stdio")
