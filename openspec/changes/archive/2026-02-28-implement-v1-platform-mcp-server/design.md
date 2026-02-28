## Context

This is the v1 implementation of the Platform MCP Server — a greenfield Python project that exposes AKS operational data to AI assistants via the Model Context Protocol. The server is used exclusively by platform engineers on their local workstations. It connects to six AKS clusters across two Azure regions (eastus, westus2) and three environments (dev, staging, prod).

Key constraints: read-only scope, stdio transport only, credentials from host environment, no network listener, all tools independently testable.

## Goals / Non-Goals

### Goals

- Deliver all six MCP tools defined in the PRD with full test coverage
- Establish the project infrastructure (src layout, CI, linting, dev containers) as a foundation for v2+
- Ensure every tool degrades gracefully when a data source is unavailable
- Meet latency targets (3–6s P95 per tool) for interactive AI assistant use

### Non-Goals

- Write operations of any kind
- Web UI, REST API, or any transport beyond stdio
- Multi-user/shared server deployment
- ArgoCD, Kong, or Datadog integrations (v2+ scope)
- Custom metrics collection or dashboarding

## Decisions

### D1: `src/` layout with `platform_mcp_server` package

**Decision**: Use PEP 517/518 `src/` layout.
**Why**: Prevents import ambiguity during testing (importing `config` from project vs. stdlib). Standard for modern Python projects.
**Alternatives**: Flat layout — rejected because it causes test import issues with common module names like `config` and `models`.

### D2: One client wrapper per API surface

**Decision**: Each external API gets its own client module under `clients/` (`k8s_core.py`, `k8s_metrics.py`, `k8s_events.py`, `k8s_policy.py`, `azure_aks.py`). Tools never call the raw SDK directly.
**Why**: Enables mocking at the client boundary for tests. Each client handles authentication, context switching, and error translation independently. Tools compose clients without knowing SDK details.
**Alternatives**: Direct SDK calls in tools — rejected because it makes mocking complex and couples tool logic to SDK internals.

### D3: Cluster context resolved per-call from config mapping

**Decision**: Every API call explicitly resolves the kubeconfig context and Azure subscription/resource-group from `config.py` before making the request. The server never relies on the current active kubectl context.
**Why**: When `cluster="all"` fans out to six clusters concurrently, each coroutine must use the correct context. Relying on the global active context would cause cross-cluster contamination.
**Alternatives**: Context switching via `kubectl config use-context` — rejected because it mutates global state and is not thread-safe.

### D4: `asyncio` for parallel cluster queries

**Decision**: Use `asyncio.gather()` for `cluster="all"` fan-out. Each tool's internal implementation is async.
**Why**: FastMCP natively supports async tool handlers. `asyncio` avoids thread-pool overhead and integrates naturally with the MCP event loop. Six concurrent I/O-bound API calls is the primary parallelism pattern.
**Alternatives**: `ThreadPoolExecutor` — viable but adds unnecessary complexity when the framework already provides an async runtime.

### D5: Pydantic v2 for all tool I/O and error models

**Decision**: Every tool input, output, and error is a Pydantic v2 `BaseModel`. No raw dicts cross tool boundaries.
**Why**: Schema validation, serialization, and documentation generation. The `ToolError` model ensures consistent error shapes across all tools. Pydantic v2's performance is sufficient for the data volumes involved.
**Alternatives**: TypedDict — rejected because it lacks runtime validation, which is needed for tool parameter validation from LLM-generated input.

### D6: structlog for all logging

**Decision**: Use `structlog` configured for JSON output to stderr. No stdlib `logging` or `print()`.
**Why**: MCP clients (Claude Desktop, Claude Code) capture stderr for log display. JSON-structured logs enable parsing and filtering. `structlog` provides context binding (tool name, cluster, latency) without boilerplate.
**Alternatives**: stdlib `logging` with JSON formatter — works but requires more boilerplate for structured context and lacks `structlog`'s processor pipeline.

### D7: Historical upgrade data from AKS Activity Log, not Kubernetes Events API

**Decision**: Current in-progress upgrade timing uses Kubernetes Events API (`NodeUpgrade`/`NodeReady` event deltas). Historical upgrade durations use AKS Activity Log (90-day retention).
**Why**: Kubernetes events have a ~1 hour TTL. A completed upgrade's events are gone within an hour. AKS Activity Log retains records for 90 days, providing the historical baseline needed for `get_upgrade_duration_metrics`.
**Alternatives**: Persisting event data to a local database — rejected as over-engineering for v1; adds state management complexity.

### D8: 60-minute anomaly threshold as configurable baseline

**Decision**: The ADO pipeline + Terraform upgrade process is designed to complete within 60 minutes. Any estimated or elapsed duration beyond this is flagged as potentially anomalous. The threshold is configurable in `config.py`.
**Why**: Gives engineers a concrete signal to investigate. Suppressed when the cause is a known PDB block (not truly anomalous).
**Alternatives**: No threshold / purely informational — rejected because the 60-minute baseline is a real operational contract with the team.

## Risks / Trade-offs

- **AKS API rate limiting during `cluster="all"` (6 concurrent calls)** → Mitigated by 30s TTL cache on AKS API responses; retry with exponential backoff
- **Kubernetes `metrics-server` unavailable in a cluster** → Mitigated by graceful degradation in `check_node_pool_pressure` (returns Core API data with structured error note)
- **Node pool label (`agentpool`) missing on some nodes** → Mitigated by fallback to `kubernetes.azure.com/agentpool` label; warning in output if neither found
- **PDB `live` mode may not detect blocks before kubelet issues eviction** → Mitigated by supplementing event detection with direct PDB satisfiability evaluation
- **50-pod result cap in `get_pod_health` may hide tail failures** → Acceptable trade-off; grouped summary by failure reason always includes full counts; individual pod list is capped

## Migration Plan

Not applicable — greenfield implementation. No existing code or data to migrate.

## Open Questions

None — all open questions from the PRD that affect v1 implementation have been resolved through the clarification process. Remaining OQs (OQ-01, OQ-02, OQ-03, OQ-05, OQ-07, OQ-08, OQ-09) are deferred to v2 scoping or will be resolved during implementation based on team feedback.
