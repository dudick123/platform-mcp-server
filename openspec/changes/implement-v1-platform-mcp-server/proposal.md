# Change: Implement v1 Platform MCP Server

## Why

Platform engineers managing 100+ tenants across six AKS clusters (three environments, two regions) currently context-switch between ArgoCD, kubectl, the Azure Portal, and Azure DevOps pipelines to answer operational questions. A single triage question — "why are pods pending in prod-eastus?" — requires running multiple kubectl commands and cross-referencing node, pod, and event state manually. During incidents, this adds minutes of triage overhead per engineer per event.

The Platform MCP Server eliminates this by exposing read-only operational data through the Model Context Protocol, giving AI assistants (Claude Desktop, Claude Code, Cursor) direct access to cluster health, upgrade state, and pod diagnostics in a single conversational turn.

## What Changes

This is the full v1 implementation — greenfield, no existing code.

### New Capabilities

1. **MCP Server Core** — FastMCP server scaffolding with stdio transport, Pydantic models for all inputs/outputs, structured error handling, multi-cluster config, and `structlog` JSON logging
2. **Node Pool Pressure Monitoring** (`check_node_pool_pressure`) — CPU/memory request ratios and pending pod counts per node pool, with configurable pressure thresholds (ok/warning/critical)
3. **Pod Health Diagnostics** (`get_pod_health`) — Failed and pending pod listing with failure reason grouping, OOMKill detection, event context, namespace/status/lookback filtering, and 50-pod result cap
4. **Kubernetes Upgrade Status** (`get_kubernetes_upgrade_status`) — Control plane and node pool version state, available upgrades, support status, deprecated version flagging across all clusters
5. **Upgrade Progress Tracking** (`get_upgrade_progress`) — Per-node upgrade state (upgraded/upgrading/cordoned/pdb_blocked/pending/stalled) during in-flight upgrades, with elapsed time and estimated remaining duration
6. **Upgrade Duration Metrics** (`get_upgrade_duration_metrics`) — Current run timing from Kubernetes Events API, historical baselines from AKS Activity Log (90-day retention), 60-minute anomaly flagging
7. **PDB Upgrade Risk Check** (`check_pdb_upgrade_risk`) — Preflight mode evaluates PDB drain-block risk before upgrades; live mode identifies PDBs actively blocking eviction on cordoned nodes during in-flight upgrades

### Project Infrastructure

- `src/` layout (PEP 517/518) with `platform_mcp_server` package
- `uv` for package management with committed `uv.lock`
- Ruff (linting + formatting), mypy (strict type checking), pre-commit hooks
- pytest with pytest-asyncio and pytest-cov (90% coverage floor)
- Dev container configuration for repeatable environments
- VS Code workspace settings and recommended extensions
- Azure DevOps Pipelines CI configuration

## Impact

- Affected specs: All new — `mcp-server-core`, `node-pool-pressure`, `pod-health`, `k8s-upgrade-status`, `upgrade-progress`, `upgrade-duration-metrics`, `pdb-upgrade-risk`
- Affected code: Entire `src/platform_mcp_server/` package (new), `tests/` (new), `pyproject.toml` (new), `.devcontainer/` (new), `.vscode/` (new), `.pre-commit-config.yaml` (new), `azure-pipelines.yml` (new)
- No breaking changes (greenfield)
- All tools are read-only — no write operations against any cluster, Git, or pipeline system
