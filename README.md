# Platform MCP Server

Expose AKS operational data to AI assistants via the [Model Context Protocol](https://modelcontextprotocol.io/).

Platform MCP Server gives platform engineers natural-language access to monitoring, diagnostics, and upgrade-tracking across a multi-tenant AKS fleet — without leaving their AI assistant.

## Features

- **Node pool pressure** — CPU/memory utilization, pending pods, and autoscaler headroom per pool
- **Pod health diagnostics** — Failed and pending pods grouped by failure category (scheduling, runtime, registry, config) with OOMKill detection
- **Kubernetes upgrade status** — Control plane and node pool versions, available upgrades, deprecated version warnings
- **Upgrade progress tracking** — Per-node state (upgraded/upgrading/cordoned/stalled), elapsed time, estimated remaining, anomaly flags
- **Upgrade duration metrics** — Current and historical timing with P90 baselines from Azure Activity Log
- **PDB upgrade risk** — Preflight and live detection of PodDisruptionBudgets that could block node drains
- **Fleet-wide queries** — Pass `cluster="all"` to fan out across all 6 clusters in parallel
- **LLM-safe output** — Structured JSON, no stack traces, sensitive data scrubbed automatically

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  AI Assistant (Claude, Cursor, etc.)                    │
│                        ▲                                │
│                        │ stdio (MCP protocol)           │
│                        ▼                                │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Platform MCP Server                             │   │
│  │                                                  │   │
│  │  server.py ─── FastMCP tool registration         │   │
│  │      │                                           │   │
│  │  tools/    ─── One tool per module               │   │
│  │      │         (node_pools, pod_health, ...)     │   │
│  │      │                                           │   │
│  │  clients/  ─── One client per API surface        │   │
│  │      │         (k8s_core, k8s_metrics, azure)    │   │
│  │      │                                           │   │
│  │  models.py ─── Pydantic v2 I/O schemas           │   │
│  │  config.py ─── Cluster map & thresholds          │   │
│  └──────────────────────────────────────────────────┘   │
│                  │                    │                  │
│         Kubernetes APIs         Azure ARM APIs          │
│     (Core, Metrics, Policy,    (AKS, Activity Log)      │
│          Events)                                        │
└─────────────────────────────────────────────────────────┘
```

**Key design decisions:**

- **Read-only** — No write operations; safe to expose to AI assistants
- **stdio transport** — No network listener; runs as a local subprocess per engineer
- **One tool per module** — Each tool is independently testable and deployable
- **Structured errors** — Pydantic `ToolError` model replaces stack traces with actionable messages
- **Graceful degradation** — Partial results returned when individual data sources fail

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager
- Azure CLI authenticated (`az login`)
- Kubeconfig with contexts for your AKS clusters (`az aks get-credentials`)

## Getting started

### 1. Clone and install

```bash
git clone https://github.com/dudick123/platform-mcp-server.git
cd platform-mcp-server
uv sync
```

### 2. Configure clusters

Edit `src/platform_mcp_server/config.py` and replace the placeholder subscription IDs with your real values:

```python
CLUSTER_MAP = {
    "dev-eastus": ClusterConfig(
        cluster_id="dev-eastus",
        environment="dev",
        region="eastus",
        subscription_id="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",  # your subscription
        resource_group="rg-dev-eastus",
        aks_cluster_name="aks-dev-eastus",
        kubeconfig_context="aks-dev-eastus",
    ),
    # ... configure remaining clusters
}
```

### 3. Authenticate

```bash
az login
az aks get-credentials --resource-group rg-dev-eastus --name aks-dev-eastus
# Repeat for each cluster
```

### 4. Run the server

```bash
python -m platform_mcp_server.server
```

The server reads from stdin and writes to stdout using the MCP protocol. Logs go to stderr.

## Client integration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "platform-mcp-server": {
      "command": "uv",
      "args": [
        "run", "--directory", "/path/to/platform-mcp-server",
        "python", "-m", "platform_mcp_server.server"
      ]
    }
  }
}
```

### Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "platform-mcp-server": {
      "command": "uv",
      "args": [
        "run", "--directory", "/path/to/platform-mcp-server",
        "python", "-m", "platform_mcp_server.server"
      ]
    }
  }
}
```

### VS Code / Cursor

Configure via the MCP server settings in your editor, pointing to the same `uv run` command.

## Tools reference

### `check_node_pool_pressure`

Check CPU and memory pressure for node pools in a cluster.

```
cluster: "prod-eastus"  # or "all" for fleet-wide
```

Returns per-pool CPU request ratio, memory request ratio, pending pod count, ready/max node counts, and a pressure level (`ok`, `warning`, `critical`).

### `get_pod_health`

Get diagnostics for failed and pending pods.

```
cluster: "prod-eastus"
namespace: "default"        # optional — omit for all namespaces
status_filter: "all"        # "pending", "failed", or "all"
```

Returns pods grouped by failure category with restart counts, event context, and OOMKill detection. Capped at 50 pods.

### `get_kubernetes_upgrade_status`

Get Kubernetes version and upgrade availability.

```
cluster: "all"  # fleet-wide version table
```

Returns control plane version, per-pool versions, available upgrades, support status, and in-flight upgrade detection.

### `get_upgrade_progress`

Track per-node progress during an in-flight upgrade.

```
cluster: "prod-eastus"
node_pool: "system"     # optional — omit for all pools
```

Returns each node's state (`upgraded`, `upgrading`, `cordoned`, `pdb_blocked`, `pending`, `stalled`), elapsed/estimated time, and anomaly flags.

### `get_upgrade_duration_metrics`

Get current and historical upgrade timing.

```
cluster: "prod-eastus"
node_pool: "system"
history_count: 5        # 1–50, number of historical records
```

Returns current elapsed time, per-node timing from Events API, and historical durations with mean/P90/min/max from Activity Log.

### `check_pdb_upgrade_risk`

Check PodDisruptionBudget risks that could block upgrades.

```
cluster: "prod-eastus"
node_pool: "system"         # optional — omit for cluster-wide
mode: "preflight"           # "preflight" or "live"
```

- **preflight** — Evaluate all PDBs for drain-block risk before starting an upgrade
- **live** — Identify PDBs currently blocking eviction on cordoned nodes

## Usage examples

Once connected to your AI assistant, interact using natural language:

> **"Check node pool pressure across all production clusters"**
>
> The assistant calls `check_node_pool_pressure(cluster="all")` and returns a summary of CPU/memory utilization per pool, highlighting any pools at warning or critical levels.

> **"Are there any failing pods in the default namespace on staging-eastus?"**
>
> The assistant calls `get_pod_health(cluster="staging-eastus", namespace="default")` and groups results by failure category — scheduling issues, image pull errors, OOMKills, etc.

> **"What Kubernetes versions are running across the fleet?"**
>
> The assistant calls `get_kubernetes_upgrade_status(cluster="all")` and presents a version table showing each cluster's control plane version, node pool versions, and available upgrades.

> **"The prod-eastus upgrade seems stuck. What's happening?"**
>
> The assistant calls `get_upgrade_progress(cluster="prod-eastus")` to show per-node states, then `check_pdb_upgrade_risk(cluster="prod-eastus", mode="live")` to identify any PDBs blocking the drain.

> **"How long did the last 5 upgrades take for the system pool on prod-eastus?"**
>
> The assistant calls `get_upgrade_duration_metrics(cluster="prod-eastus", node_pool="system", history_count=5)` and returns a timing summary with P90 baselines.

## Configuration

Thresholds are configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PRESSURE_CPU_WARNING` | `75` | CPU request ratio (%) to trigger warning |
| `PRESSURE_CPU_CRITICAL` | `90` | CPU request ratio (%) to trigger critical |
| `PRESSURE_MEMORY_WARNING` | `80` | Memory request ratio (%) to trigger warning |
| `PRESSURE_MEMORY_CRITICAL` | `95` | Memory request ratio (%) to trigger critical |
| `PRESSURE_PENDING_PODS_WARNING` | `1` | Pending pod count to trigger warning |
| `PRESSURE_PENDING_PODS_CRITICAL` | `10` | Pending pod count to trigger critical |
| `UPGRADE_ANOMALY_MINUTES` | `60` | Minutes before an upgrade is flagged as stalled |

## Project structure

```
src/platform_mcp_server/
├── server.py              # MCP entry point and tool registration
├── config.py              # Cluster mappings and thresholds
├── models.py              # Pydantic v2 I/O schemas
├── validation.py          # Input validation (namespace, node pool, mode)
├── tools/
│   ├── node_pools.py      # check_node_pool_pressure
│   ├── pod_health.py      # get_pod_health
│   ├── k8s_upgrades.py    # get_kubernetes_upgrade_status
│   ├── upgrade_progress.py# get_upgrade_progress
│   ├── upgrade_metrics.py # get_upgrade_duration_metrics
│   ├── pdb_check.py       # check_pdb_upgrade_risk
│   └── pod_classification.py  # Shared failure categorization
└── clients/
    ├── k8s_core.py        # Nodes, pods, namespaces (Core v1)
    ├── k8s_metrics.py     # CPU/memory usage (metrics.k8s.io)
    ├── k8s_events.py      # Upgrade and pod events (Core v1)
    ├── k8s_policy.py      # PodDisruptionBudgets (policy/v1)
    └── azure_aks.py       # Cluster info, versions, activity log
```

## Development

### Setup

```bash
uv sync
uv run pre-commit install
```

### Run checks

```bash
uv run ruff check .                         # Lint
uv run ruff format --check .                # Format check
uv run mypy src/                            # Type check (strict mode)
uv run pytest --cov --cov-report=term       # Tests with coverage (90% minimum)
```

### Dev container

A dev container configuration is included for VS Code. Open the repo and select **"Reopen in Container"** for a pre-configured environment with Python, uv, Azure CLI, and kubectl. Your `~/.azure` and `~/.kube` directories are mounted read-only.

> [!NOTE]
> The server uses **stdio transport only** — it runs as a local subprocess per engineer and does not expose a network listener. All operations are read-only.
