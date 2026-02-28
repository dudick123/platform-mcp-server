"""MCP server entry point and tool registration."""

# Note 1: MCP (Model Context Protocol) is an open standard that lets LLMs discover and
# Note 2: invoke tools exposed by external servers. Instead of hard-coding API calls inside
# Note 3: a model, MCP separates the tool interface from the model, so any compliant client
# Note 4: (Claude Desktop, Claude Code, etc.) can call tools defined here without changes.

from __future__ import annotations

import sys
import time

import structlog

# Note 5: FastMCP is Anthropic's high-level Python SDK that wraps the low-level MCP wire
# Note 6: protocol. It converts plain async functions into MCP-compliant tool descriptors
# Note 7: automatically, so you write ordinary Python and get a standards-compliant server.
from mcp.server.fastmcp import FastMCP

from platform_mcp_server.config import validate_cluster_config
from platform_mcp_server.models import scrub_sensitive_values
from platform_mcp_server.tools.k8s_upgrades import get_upgrade_status_all, get_upgrade_status_handler
from platform_mcp_server.tools.node_pools import check_node_pool_pressure_all, check_node_pool_pressure_handler
from platform_mcp_server.tools.pdb_check import check_pdb_risk_all, check_pdb_risk_handler
from platform_mcp_server.tools.pod_health import get_pod_health_all, get_pod_health_handler
from platform_mcp_server.tools.upgrade_metrics import get_upgrade_metrics_all, get_upgrade_metrics_handler
from platform_mcp_server.tools.upgrade_progress import get_upgrade_progress_all, get_upgrade_progress_handler

# Note 8: structlog.configure() is called at module level (top of file, outside any function)
# Note 9: so that every logger created anywhere in the process inherits the same processor
# Note 10: pipeline. Configuring inside a function risks the setting being applied too late
# Note 11: or multiple times if the function is called more than once.
# Configure structlog for JSON output to stderr
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        # Note 12: ConsoleRenderer produces colourised, human-readable output when stderr is a
        # Note 13: real terminal (isatty() is True). JSONRenderer is used otherwise, e.g. in CI
        # Note 14: pipelines or container runtimes where logs are consumed by a log aggregator.
        # Note 15: This single expression selects the right renderer without an explicit if/else.
        structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

log = structlog.get_logger()

# Note 16: FastMCP("Platform MCP Server") creates the server instance. The string argument
# Note 17: becomes the server name advertised to MCP clients during capability negotiation.
mcp = FastMCP("Platform MCP Server")


# Note 18: @mcp.tool() is a decorator that registers the decorated async function as an MCP
# Note 19: tool. FastMCP derives the tool name from the Python function name and uses the
# Note 20: function's docstring as the tool description shown to the LLM. Type annotations on
# Note 21: the parameters are converted to a JSON Schema that the client uses for validation.
@mcp.tool()
async def check_node_pool_pressure(cluster: str) -> str:
    """Check CPU and memory pressure levels for node pools in an AKS cluster.

    Returns per-pool CPU request ratio, memory request ratio, pending pod count,
    ready node count, max node count, and a pressure level (ok/warning/critical).
    Use this when investigating node resource exhaustion or autoscaler headroom.

    Args:
        cluster: Cluster ID (e.g., 'prod-eastus') or 'all' for fleet-wide query.
    """
    # Note 22: time.monotonic() returns a float of seconds from an arbitrary but fixed epoch.
    # Note 23: Unlike time.time(), it is guaranteed never to go backwards, even if the system
    # Note 24: clock is adjusted (e.g. by NTP). This makes it safe for measuring elapsed time.
    start = time.monotonic()
    try:
        # Note 25: cluster == "all" is the fan-out entry point. A single tool call with this
        # Note 26: sentinel value dispatches to a handler that queries every configured cluster
        # Note 27: concurrently and returns a list of results, one per cluster. All other values
        # Note 28: are treated as a specific cluster ID and dispatched to the single handler.
        if cluster == "all":
            results = await check_node_pool_pressure_all()
            output = "\n\n".join(scrub_sensitive_values(r.model_dump_json(indent=2)) for r in results)
        else:
            result = await check_node_pool_pressure_handler(cluster)
            # Note 29: scrub_sensitive_values() is applied to every output string before it is
            # Note 30: returned to the LLM. This is a defence-in-depth measure: even if a tool
            # Note 31: handler accidentally includes a secret in its output, the scrubber will
            # Note 32: redact it before the data crosses the boundary into the model context.
            output = scrub_sensitive_values(result.model_dump_json(indent=2))
        log.info("tool_completed", tool="check_node_pool_pressure", cluster=cluster, latency_ms=_elapsed_ms(start))
        return output
    except Exception as e:
        sanitised = scrub_sensitive_values(str(e))
        log.error("tool_failed", tool="check_node_pool_pressure", cluster=cluster, error=sanitised)
        # Note 33: "raise RuntimeError(sanitised) from None" does two things. First, it wraps
        # Note 34: the error in a plain RuntimeError with a scrubbed message so no internal
        # Note 35: details leak to the caller. Second, "from None" suppresses the implicit
        # Note 36: exception chain (__cause__ and __context__), preventing Python from printing
        # Note 37: "During handling of the above exception, another exception occurred", which
        # Note 38: could expose the original traceback and internal error messages to the LLM.
        raise RuntimeError(sanitised) from None


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
    lookback_minutes = max(1, min(lookback_minutes, 1440))
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
        sanitised = scrub_sensitive_values(str(e))
        log.error("tool_failed", tool="get_pod_health", cluster=cluster, error=sanitised)
        raise RuntimeError(sanitised) from None


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
        sanitised = scrub_sensitive_values(str(e))
        log.error("tool_failed", tool="get_kubernetes_upgrade_status", cluster=cluster, error=sanitised)
        raise RuntimeError(sanitised) from None


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
        sanitised = scrub_sensitive_values(str(e))
        log.error("tool_failed", tool="get_upgrade_progress", cluster=cluster, error=sanitised)
        raise RuntimeError(sanitised) from None


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
    history_count = max(1, min(history_count, 50))
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
        sanitised = scrub_sensitive_values(str(e))
        log.error("tool_failed", tool="get_upgrade_duration_metrics", cluster=cluster, error=sanitised)
        raise RuntimeError(sanitised) from None


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
        sanitised = scrub_sensitive_values(str(e))
        log.error("tool_failed", tool="check_pdb_upgrade_risk", cluster=cluster, error=sanitised)
        raise RuntimeError(sanitised) from None


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

# Note 39: The "if __name__ == '__main__'" guard is a standard Python idiom that lets a module
# Note 40: serve dual purposes: it can be imported by other modules (in which case __name__
# Note 41: equals the module's dotted name and the block is skipped), or run directly as a
# Note 42: script (in which case __name__ equals "__main__" and the block executes).
# Note 43: mcp.run(transport="stdio") starts the MCP server using stdin/stdout as the
# Note 44: communication channel. The stdio transport is chosen over HTTP because this server
# Note 45: is designed to run as a subprocess launched by a single MCP client (one process per
# Note 46: engineer's workstation). There is no need for a network listener, and stdio avoids
# Note 47: port conflicts, firewall rules, and TLS certificate management entirely.
if __name__ == "__main__":
    # Note 48: validate_cluster_config() is called here — after import but before serving
    # Note 49: any requests — so a misconfigured deployment (placeholder subscription IDs)
    # Note 50: fails immediately with a clear error rather than silently making real Azure
    # Note 51: API calls with invalid credentials at the first tool invocation.
    validate_cluster_config()
    mcp.run(transport="stdio")
