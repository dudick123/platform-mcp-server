# Project Context

## Purpose

The Platform MCP Server is an internal tool that exposes GitOps platform operational data to AI assistants (Claude Desktop, Claude Code, Cursor) via the Model Context Protocol (MCP). It gives platform engineers natural-language access to monitoring, diagnostics, and upgrade-tracking capabilities across an AKS multi-tenant platform — eliminating context-switching between ArgoCD, kubectl, and the Azure Portal.

All tools are **read-only** in v1. No writes to cluster, Git, or pipeline state are in scope.

### Primary Use Cases (v1)
1. **Node pool pressure monitoring** — CPU/memory utilization and autoscaler headroom
2. **Kubernetes version upgrade tracking** — version state, in-flight upgrades, per-node status
3. **Upgrade duration metrics** — elapsed time, estimated remaining, historical baselines
4. **Failed and pending pod diagnostics** — root cause grouping by failure reason
5. **PDB preflight and live drain-blocker detection** — PodDisruptionBudget risk before and during upgrades

### Target Clusters
Three environments: `dev`, `staging`, `prod`. 100+ tenants across all environments.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.14+ | Matches existing platform tooling |
| MCP Framework | `mcp[cli]` (FastMCP) | Official Anthropic SDK; simplest tool definition pattern |
| Package Manager | `uv` | Fast, lockfile-based; consistent with platform Python tooling |
| Kubernetes Client | `kubernetes` (official Python client) | Core, Metrics, Events, and Policy APIs |
| Azure Client | `azure-mgmt-containerservice` + `azure-identity` | AKS management plane; `DefaultAzureCredential` |
| Validation | Pydantic v2 | All tool inputs/outputs use Pydantic models |
| Transport | stdio (local only) | No network listener; credentials stay local |
| Testing | pytest | Per-tool test files under `tests/` |

---

## Project Layout

```
platform-mcp-server/
├── server.py                   # MCP entry point; tool registrations
├── config.py                   # Thresholds, cluster→resource-group mapping, kubeconfig context map
├── models.py                   # Pydantic models for all tool inputs/outputs
├── tools/
│   ├── node_pools.py           # check_node_pool_pressure
│   ├── pod_health.py           # get_pod_health
│   ├── k8s_upgrades.py         # get_kubernetes_upgrade_status
│   ├── upgrade_progress.py     # get_upgrade_progress (per-node state)
│   ├── upgrade_metrics.py      # get_upgrade_duration_metrics
│   └── pdb_check.py            # check_pdb_upgrade_risk (preflight + live)
├── clients/
│   ├── k8s_core.py             # Kubernetes Core API wrapper (nodes, pods, namespaces)
│   ├── k8s_metrics.py          # Kubernetes Metrics API wrapper (CPU/memory usage)
│   ├── k8s_events.py           # Kubernetes Events API wrapper (NodeUpgrade, NodeReady)
│   ├── k8s_policy.py           # Kubernetes Policy API wrapper (PodDisruptionBudgets)
│   └── azure_aks.py            # AKS REST API wrapper (versions, upgrade profiles, activity log)
├── tests/
│   ├── test_node_pools.py
│   ├── test_pod_health.py
│   ├── test_k8s_upgrades.py
│   ├── test_upgrade_progress.py
│   ├── test_upgrade_metrics.py
│   └── test_pdb_check.py
├── openspec/
└── pyproject.toml
```

---

## Project Conventions

### Code Style
- Python 3.11+ with type hints on all public functions and class attributes
- Pydantic v2 models for **all** tool inputs and outputs — no raw dicts in tool signatures
- Tool docstrings are used by the LLM for tool selection; they must accurately describe when and how to invoke the tool
- `config.py` is the single source of truth for thresholds, cluster mappings, and kubeconfig context names — no hardcoded values in tool modules

### Architecture Patterns
- **One tool per module** under `tools/` — each tool is independently testable
- **One client per API surface** under `clients/` — tools call clients, never the raw Kubernetes/Azure SDK directly
- **Cluster context resolved at call time** — always look up the kubeconfig context from `config.py` mapping before making any Kubernetes API call; never rely on the active context
- **Parallel cluster queries** — when `cluster="all"`, fan out requests concurrently using `asyncio` or `ThreadPoolExecutor`; do not call clusters sequentially
- **Graceful degradation** — if a data source is unavailable (e.g., Metrics API down), return available data from other sources with a structured error note; do not raise unhandled exceptions
- **Structured errors, not stack traces** — all exceptions caught at the tool boundary; return a Pydantic error model suitable for LLM consumption
- **Short TTL caching (30s)** on AKS API calls to prevent rate limiting during parallel cluster queries; use per-tool cache

### Authentication
- Azure: `DefaultAzureCredential` — inherits the engineer's `az login` session; no service principal credentials stored locally
- Kubernetes: kubeconfig from `KUBECONFIG` env var, context explicitly set from `config.py` mapping per call
- No credentials in logs, tool output, or error messages

### Logging
- Structured JSON to `stderr` (MCP clients forward stderr to their log facilities)
- Each tool invocation logs: tool name, parameters, data source latency, success/failure
- No PII, internal IPs, or credential values in logs

### Tool Output Format
- Every tool response includes a human-readable summary line suitable for LLM context
- Results are scrubbed of internal IP addresses and sensitive resource identifiers before return
- Timestamps included in all responses so the LLM can contextualize data freshness

### Pressure Level Thresholds (externalized to `config.py`)

| Level | CPU Requests / Allocatable | Memory Requests / Allocatable | Pending Pods |
|---|---|---|---|
| Critical | ≥ 90% | ≥ 95% | > 10 |
| Warning | ≥ 75% | ≥ 80% | > 0 |
| OK | < 75% | < 80% | 0 |

### Upgrade State Model
Node-level states used in `get_upgrade_progress`:
`upgraded` · `upgrading` · `cordoned` · `pdb_blocked` · `pending` · `stalled`

A node is `stalled` when the total pool upgrade has exceeded 60 minutes (configurable in `config.py`) and the node is not yet `NodeReady` with no active PDB block.

### Testing Strategy

This project follows **Test-Driven Development (TDD)**. Tests are written before implementation code. No implementation is considered complete unless all relevant tests pass.

#### TDD Cycle

1. **Red** — Write a failing test that specifies the desired behavior. The test must fail for the right reason (not a syntax error).
2. **Green** — Write the minimum implementation code required to make the test pass. Do not over-engineer.
3. **Refactor** — Clean up the implementation and tests. Run the suite after every change.

#### Rules

- **Tests first, always** — do not write implementation code for a new behavior until a failing test exists for it
- **No implementation without a test** — if a behavior is not covered by a test, it is not considered implemented
- **Tests live alongside the code they cover** — one test file per tool module (`tests/test_<tool>.py`) and one per client module (`tests/test_clients/<client>.py`)
- **Mock all external I/O** — Kubernetes and Azure API clients must be mocked in every test; no live cluster calls in tests
- **Test the contract, not the implementation** — assert on Pydantic output models and error structures, not internal helper functions
- **Failure modes are first-class** — each tool must have explicit tests for: data source unavailable (e.g., Metrics API down), partial API response, invalid parameters, and all error states defined in the PRD

#### Test Coverage Requirements

Every tool module must have tests covering:

- Happy path with realistic fixture data
- Graceful degradation when a backing API is unavailable
- Pressure/state threshold boundaries (e.g., exactly at `warning` vs. `critical` cutoffs)
- `cluster="all"` parallel fan-out behavior
- Structured error output format (not raw exceptions)

#### Running Tests

```bash
uv run pytest              # full suite
uv run pytest tests/test_node_pools.py  # single module
uv run pytest --tb=short   # compact failure output
```

### Git Workflow

**AI agents must not perform any git operations.** This includes — but is not limited to — creating branches, committing, pushing, pulling, rebasing, merging, tagging, or interacting with any remote. All git operations are the exclusive responsibility of the human engineer.

- Branch from `main`; PR required for all changes
- Commit messages: conventional commits style (`feat:`, `fix:`, `refactor:`, `test:`, `chore:`)
- OpenSpec proposals required before implementing new tools or breaking changes (see `openspec/AGENTS.md`)
- The engineer creates the branch before starting work and pushes when ready — the agent only reads and writes files

---

## Domain Context

- **GitOps platform**: ArgoCD / Akuity SaaS for application sync; Azure DevOps pipelines + Terraform for AKS upgrade orchestration
- **Upgrade cadence**: dev → staging → prod wave order; ADO pipeline upgrades expected to complete within **60 minutes** under normal conditions — this is the anomaly threshold
- **Historical upgrade data**: Sourced from AKS Activity Log (90-day retention), not Kubernetes Events API (1-hour TTL). Events API is used only for current in-progress run timing.
- **Node pool identification**: Nodes are grouped by the `agentpool` label (fallback: `kubernetes.azure.com/agentpool`)
- **Multi-cluster kubeconfig**: Engineers run `az aks get-credentials` for all three clusters; the server resolves contexts from `config.py` — a single merged kubeconfig is the expected setup

---

## Important Constraints

- **Read-only in v1** — the server must not expose any write operations; no mutations to cluster, Git, pipeline, or ArgoCD state
- **stdio transport only** — no network listener; no web UI or REST API surface
- **No hardcoded credentials** — credentials sourced exclusively from env vars and kubeconfig
- **LLM-safe output** — tool responses are consumed directly by AI assistants; errors must be structured, not raw stack traces; output must not leak sensitive identifiers
- **Per-engineer deployment** — each engineer runs their own local process; no multi-user shared server in v1

---

## External Dependencies

| System | Purpose | Auth |
|---|---|---|
| Kubernetes Metrics API (`metrics.k8s.io/v1beta1`) | CPU/memory usage per node | kubeconfig (`az aks get-credentials`) |
| Kubernetes Core API (`v1`) | Nodes, pods, namespaces, events | kubeconfig |
| Kubernetes Policy API (`policy/v1`) | PodDisruptionBudgets | kubeconfig |
| Azure AKS REST API | Cluster versions, node pool state, upgrade profiles | `DefaultAzureCredential` (`az login`) |
| AKS Activity Log | Historical upgrade duration records (90-day retention) | `DefaultAzureCredential` |
