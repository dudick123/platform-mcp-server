# GitHub Copilot Instructions

## Project Overview

**Platform MCP Server** exposes AKS operational data to AI assistants (Claude Desktop, Claude Code, Cursor) via the [Model Context Protocol](https://modelcontextprotocol.io/). It gives platform engineers natural-language access to monitoring, diagnostics, and upgrade-tracking across a six-cluster AKS fleet — no context-switching between ArgoCD, kubectl, and the Azure Portal.

All tools are **read-only**. No writes to cluster, Git, or pipeline state.

---

## Architecture

```
AI Assistant (Claude, Cursor, etc.)
        │ stdio (MCP protocol)
        ▼
  server.py         ← FastMCP tool registration
      │
  tools/            ← One module per MCP tool
      │
  clients/          ← One module per API surface (k8s_core, k8s_metrics, azure_aks, …)
      │
  models.py         ← Pydantic v2 I/O schemas
  config.py         ← Cluster map & thresholds
        │                     │
  Kubernetes APIs       Azure ARM APIs
```

**Key design decisions:**
- `cluster="all"` fans out across all six clusters concurrently using `asyncio.gather`.
- Tool outputs are always JSON strings — no stack traces, no sensitive values.
- `ToolError` Pydantic model replaces exceptions in LLM-facing responses.
- Graceful degradation: partial results are returned when individual clusters fail.

---

## Project Layout

```
clusters.example.yaml           # Template cluster config (copy to clusters.yaml)
src/platform_mcp_server/
├── server.py               # MCP entry point; tool registrations with @mcp.tool()
├── config.py               # ClusterConfig frozen dataclasses, ThresholdConfig, YAML loader
├── models.py               # All Pydantic v2 models for tool I/O
├── validation.py           # Input validation helpers
├── utils.py                # Shared utilities (timestamp parsing)
├── tools/
│   ├── node_pools.py       # check_node_pool_pressure
│   ├── pod_health.py       # get_pod_health
│   ├── k8s_upgrades.py     # get_kubernetes_upgrade_status
│   ├── upgrade_progress.py # get_upgrade_progress
│   ├── upgrade_metrics.py  # get_upgrade_duration_metrics
│   ├── pdb_check.py        # check_pdb_upgrade_risk
│   └── pod_classification.py  # Shared failure categorization
└── clients/
    ├── k8s_core.py         # Kubernetes Core API (nodes, pods, namespaces)
    ├── k8s_metrics.py      # Kubernetes Metrics API (CPU/memory usage)
    ├── k8s_events.py       # Kubernetes Events API
    ├── k8s_policy.py       # Kubernetes Policy API (PodDisruptionBudgets)
    └── azure_aks.py        # AKS REST API (versions, upgrade profiles, activity log)

tests/
├── conftest.py             # Shared fixtures (mock K8s client, mock Azure client)
├── fixtures/               # Static JSON test data
├── test_clients/           # Per-client test files
└── test_*.py               # Per-tool test files
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.14+ |
| MCP Framework | `mcp[cli]` (FastMCP) |
| Package Manager | `uv` (no pip, no requirements.txt) |
| Kubernetes Client | `kubernetes` official Python client |
| Azure Client | `azure-mgmt-containerservice` + `azure-identity` |
| Validation | Pydantic v2 |
| Logging | `structlog` (JSON to stderr) |
| Linting & Formatting | Ruff (`line-length = 120`) |
| Type Checking | mypy strict mode |
| Security Scanning | Bandit |
| Testing | pytest + pytest-asyncio + pytest-cov (≥90% coverage enforced) |
| CI | GitHub Actions (lint + test on push/PR to main) |

---

## Coding Conventions

### General
- All files start with `from __future__ import annotations`.
- Cluster configuration is loaded from `clusters.yaml` (YAML) at startup, not hardcoded.
- Use frozen `@dataclass` for config objects — never plain dicts.
- Use Pydantic v2 models for all tool inputs and outputs.
- `structlog` for all logging — never `print()` or stdlib `logging` directly.
- `time.monotonic()` for elapsed-time measurements (not `time.time()`).

### Tool Pattern
Each MCP tool follows this structure in `server.py`:
```python
@mcp.tool()
async def tool_name(cluster: str) -> str:
    """Docstring shown to the LLM — clear, actionable, explains when to use it."""
    start = time.monotonic()
    try:
        if cluster == "all":
            results = await tool_name_all()
        else:
            results = [await tool_name_handler(cluster)]
        elapsed = time.monotonic() - start
        log.info("tool_name.ok", cluster=cluster, elapsed_ms=round(elapsed * 1000))
        return model_instance.model_dump_json()
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.error("tool_name.error", cluster=cluster, error=str(exc), elapsed_ms=round(elapsed * 1000))
        return ToolError(error=str(exc), cluster=cluster).model_dump_json()
```

### Client Pattern
Clients are async wrappers around official SDK calls. They:
- Accept a `ClusterConfig` (not a raw cluster ID string).
- Use `asyncio.to_thread()` to offload synchronous SDK calls to the thread pool, keeping the event loop non-blocking.
- Use thread-safe lazy initialization (`threading.RLock` for Azure, `threading.Lock` for K8s) to guard `_get_api` / `_get_*_client` methods.
- Raise typed exceptions on failure.
- Never return raw SDK objects — convert to plain Python types or Pydantic models first.

### Testing Pattern
- Unit tests mock at the client boundary using `unittest.mock.MagicMock`.
- Each tool has a corresponding `tests/test_<tool_name>.py`.
- Each client has a corresponding `tests/test_clients/test_<client_name>.py`.
- Shared fixtures live in `tests/conftest.py`.
- Async tests use `@pytest.mark.asyncio` (mode is `auto` in `pytest.ini_options`).
- Coverage must stay ≥ 90% (`fail_under = 90` in `pyproject.toml`).

### Dependencies
- Add runtime deps: `uv add <package>`
- Add dev deps: `uv add --group dev <package>`
- Never edit `uv.lock` manually.

---

## Clusters

Six clusters across three environments and two Azure regions:

| Cluster ID | Environment | Region |
|---|---|---|
| `dev-eastus` | dev | eastus |
| `dev-westus2` | dev | westus2 |
| `staging-eastus` | staging | eastus |
| `staging-westus2` | staging | westus2 |
| `prod-eastus` | prod | eastus |
| `prod-westus2` | prod | westus2 |

Use `cluster="all"` to fan out across all six in parallel. All tool `cluster` parameters use these composite IDs.

---

## Spec-Driven Development (OpenSpec)

This project uses OpenSpec for tracking capability specs and change proposals.

**Before implementing any new feature, breaking change, or architecture shift:**
1. Read `openspec/AGENTS.md` for the full workflow.
2. Read `openspec/project.md` for project context and conventions.
3. Check `openspec/specs/` for existing capability specs.
4. Check `openspec/changes/` for in-flight proposals.

**When to create a proposal** (before coding):
- Adding new MCP tools or capabilities
- Breaking changes to tool inputs/outputs or Pydantic models
- Architecture or pattern changes
- Performance or security work that changes behavior

**Skip the proposal** for: bug fixes, typos, formatting, dependency updates, tests for existing behavior.

Scaffold a change under `openspec/changes/<verb-led-id>/` with `proposal.md` and `tasks.md`.

---

## What NOT to Do

- Do not add write operations to cluster, Git, or pipeline state (v1 is read-only).
- Do not use `pip install` — use `uv add`.
- Do not use stdlib `logging` or `print()` — use `structlog`.
- Do not return raw Python exceptions or stack traces to MCP tool callers — use `ToolError`.
- Do not skip mypy strict mode or ruff lint rules.
- Do not write tool handler logic directly in `server.py` — keep tools in `tools/` modules.
- Do not access `ClusterConfig` fields by string key — they are frozen dataclasses with typed attributes.
