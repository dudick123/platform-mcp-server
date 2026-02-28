# Implementation Tasks

All tasks follow TDD: write failing tests first, then implement, then refactor. No task is complete until its tests pass and coverage meets the 90% threshold.

## 1. Project Scaffolding (M1)

- [x] 1.1 Initialize `pyproject.toml` with PEP 621 metadata, `[project]` dependencies, `[dependency-groups]` dev group, and all tool configuration (`[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]`, `[tool.coverage.*]`)
- [x] 1.2 Create `src/platform_mcp_server/` package with `__init__.py`, empty `server.py`, `config.py`, `models.py`
- [x] 1.3 Create `src/platform_mcp_server/tools/` package with `__init__.py` and empty module stubs for all six tools
- [x] 1.4 Create `src/platform_mcp_server/clients/` package with `__init__.py` and empty module stubs for all five clients
- [x] 1.5 Create `tests/` directory with `conftest.py` (shared fixtures: mock K8s client factory, mock Azure client factory, test cluster config), `tests/fixtures/` directory, and `tests/test_clients/` with its own `conftest.py`
- [x] 1.6 Create `.pre-commit-config.yaml` with Ruff and mypy hooks
- [x] 1.7 Create `.vscode/settings.json` and `.vscode/extensions.json` with recommended workspace configuration
- [x] 1.8 Create `.devcontainer/devcontainer.json` with Python 3.14 base image, Azure CLI and kubectl features, postCreateCommand, credential bind mounts, and VS Code customizations
- [x] 1.9 Create `azure-pipelines.yml` with lint, format check, type check, and test+coverage stages
- [x] 1.10 Run `uv sync` and verify environment installs correctly
- [x] 1.11 Run `uv run ruff check .` and `uv run mypy src/` and verify clean output on empty scaffolding
- [x] 1.12 Run `uv run pytest` and verify test discovery works (0 tests collected, no errors)

## 2. Core Server Infrastructure (M1)

- [x] 2.1 Write tests for `config.py`: cluster mapping resolution, invalid cluster ID rejection, threshold defaults, environment variable overrides
- [x] 2.2 Implement `config.py`: `ClusterConfig` dataclass, `CLUSTER_MAP` dict, `ThresholdConfig` with env var override support, `resolve_cluster()` function
- [x] 2.3 Write tests for `models.py`: `ToolError` model serialization, all tool input/output model validation, output scrubbing of IPs and subscription IDs
- [x] 2.4 Implement `models.py`: `ToolError`, `NodePoolPressureInput`, `NodePoolPressureOutput`, `PodHealthInput`, `PodHealthOutput`, `UpgradeStatusInput`, `UpgradeStatusOutput`, `UpgradeProgressInput`, `UpgradeProgressOutput`, `UpgradeDurationInput`, `UpgradeDurationOutput`, `PdbCheckInput`, `PdbCheckOutput` Pydantic v2 models
- [x] 2.5 Write tests for `server.py`: FastMCP server initialization, tool registration, stdio transport binding
- [x] 2.6 Implement `server.py`: FastMCP server instance, tool registration stubs for all six tools, structlog configuration for JSON stderr output
- [x] 2.7 Verify server starts via `uv run python -m platform_mcp_server.server` and accepts MCP stdio handshake

## 3. Kubernetes Client Wrappers (M1–M2)

- [x] 3.1 Write tests for `clients/k8s_core.py`: node listing with pool grouping, pod listing with status filtering, context resolution per cluster, error handling for unreachable cluster
- [x] 3.2 Implement `clients/k8s_core.py`: `K8sCoreClient` with `get_nodes()`, `get_pods()`, explicit kubeconfig context loading from config mapping
- [x] 3.3 Write tests for `clients/k8s_metrics.py`: node metrics retrieval, graceful degradation when metrics-server is unavailable
- [x] 3.4 Implement `clients/k8s_metrics.py`: `K8sMetricsClient` with `get_node_metrics()`, error handling for metrics-server unavailability
- [x] 3.5 Write tests for `clients/k8s_events.py`: event filtering by reason (`NodeUpgrade`, `NodeReady`, `NodeNotReady`), event filtering by involved object kind (`Pod`), timestamp parsing
- [x] 3.6 Implement `clients/k8s_events.py`: `K8sEventsClient` with `get_node_events()`, `get_pod_events()`
- [x] 3.7 Write tests for `clients/k8s_policy.py`: PDB listing, disruption budget evaluation (`disruptionsAllowed`), PDB satisfiability calculation
- [x] 3.8 Implement `clients/k8s_policy.py`: `K8sPolicyClient` with `get_pdbs()`, `evaluate_pdb_satisfiability()`
- [x] 3.9 Write tests for `clients/azure_aks.py`: cluster version retrieval, node pool state, upgrade profile, activity log query for historical durations, partial failure handling
- [x] 3.10 Implement `clients/azure_aks.py`: `AzureAksClient` with `get_cluster_info()`, `get_node_pool_state()`, `get_upgrade_profile()`, `get_activity_log_upgrades()`, using `DefaultAzureCredential`

## 4. Node Pool Pressure Tool (M2–M4)

- [x] 4.1 Write tests for `tools/node_pools.py`: happy path with fixture data, pressure level classification at exact thresholds (boundary testing), pool grouping by agentpool label, fallback label, missing label warning, graceful degradation without metrics API, `cluster="all"` parallel fan-out, output scrubbing, human-readable summary line
- [x] 4.2 Implement `tools/node_pools.py`: `check_node_pool_pressure()` tool handler composing `K8sCoreClient` and `K8sMetricsClient`, pressure level calculation (highest severity wins), result assembly into `NodePoolPressureOutput`
- [x] 4.3 Register `check_node_pool_pressure` in `server.py` with accurate tool docstring for LLM tool selection
- [x] 4.4 Verify end-to-end with a mocked single-cluster test and a mocked `cluster="all"` test
- [x] 4.5 Verify response latency target (< 3s P95) is achievable with mock timing

## 5. Pod Health Tool (M3–M5)

- [x] 5.1 Write tests for `tools/pod_health.py`: happy path, lookback semantics (currently unhealthy always included, resolved failures filtered by event time), failure reason grouping, OOMKill detection with container detail, 50-pod result cap with truncation note, namespace filtering, status_filter filtering, `cluster="all"` fan-out, event context per pod
- [x] 5.2 Implement `tools/pod_health.py`: `get_pod_health()` tool handler composing `K8sCoreClient` and `K8sEventsClient`, failure categorization against the taxonomy (scheduling/runtime/registry/config), lookback filtering logic, result cap logic, result assembly into `PodHealthOutput`
- [x] 5.3 Register `get_pod_health` in `server.py` with accurate tool docstring
- [x] 5.4 Verify end-to-end with mocked tests covering all failure reason categories

## 6. Kubernetes Upgrade Status Tool (M6)

- [x] 6.1 Write tests for `tools/k8s_upgrades.py`: happy path with version data, active upgrade detection, deprecated version flagging (at and near end-of-support), `cluster="all"` fan-out, partial data on single cluster API failure
- [x] 6.2 Implement `tools/k8s_upgrades.py`: `get_kubernetes_upgrade_status()` tool handler composing `AzureAksClient`, version comparison logic, support status derivation, result assembly into `UpgradeStatusOutput`
- [x] 6.3 Register `get_kubernetes_upgrade_status` in `server.py` with accurate tool docstring
- [x] 6.4 Verify end-to-end with mocked tests including partial failure scenario

## 7. PDB Upgrade Risk Tool (M7)

- [x] 7.1 Write tests for `tools/pdb_check.py`: preflight mode — PDB with maxUnavailable=0 flagged, PDB with minAvailable=ready count flagged, PDB with available budget not flagged, node_pool filtering in preflight, cluster-wide evaluation when node_pool omitted; live mode — active block on cordoned node detected, no active blocks returns clean result; `cluster="all"` fan-out
- [x] 7.2 Implement `tools/pdb_check.py`: `check_pdb_upgrade_risk()` tool handler composing `K8sPolicyClient` and `K8sCoreClient`, preflight satisfiability evaluation, live mode eviction block detection with direct PDB evaluation, node_pool filtering logic, result assembly into `PdbCheckOutput`
- [x] 7.3 Register `check_pdb_upgrade_risk` in `server.py` with accurate tool docstring
- [x] 7.4 Verify end-to-end with mocked tests for both modes

## 8. Upgrade Progress Tool (M8)

- [x] 8.1 Write tests for `tools/upgrade_progress.py`: per-node state classification (all six states), duration estimation (mean × remaining), anomaly flagging with and without PDB block, pdb_blocked state cross-reference to `check_pdb_upgrade_risk`, no upgrade in progress response, `cluster="all"` fan-out
- [x] 8.2 Implement `tools/upgrade_progress.py`: `get_upgrade_progress()` tool handler composing `AzureAksClient`, `K8sCoreClient`, `K8sEventsClient`, and `K8sPolicyClient`, node state classification logic, duration estimation, anomaly flag logic, result assembly into `UpgradeProgressOutput`
- [x] 8.3 Register `get_upgrade_progress` in `server.py` with accurate tool docstring
- [x] 8.4 Verify end-to-end with mocked tests covering all six node states

## 9. Upgrade Duration Metrics Tool (M9)

- [x] 9.1 Write tests for `tools/upgrade_metrics.py`: current run timing from Events API, historical data from Activity Log, statistical summary (mean, P90, min, max), 60-minute anomaly flag, fewer historical records than requested, no active upgrade (history only), `cluster="all"` fan-out
- [x] 9.2 Implement `tools/upgrade_metrics.py`: `get_upgrade_duration_metrics()` tool handler composing `K8sEventsClient` and `AzureAksClient`, per-node timing calculation, historical record retrieval, statistical computation, anomaly flagging, result assembly into `UpgradeDurationOutput`
- [x] 9.3 Register `get_upgrade_duration_metrics` in `server.py` with accurate tool docstring
- [x] 9.4 Verify end-to-end with mocked tests including edge cases

## 10. Integration and Completion (M10–M11)

- [x] 10.1 Write integration tests verifying all six tools are registered and respond to well-formed MCP requests via the FastMCP test client
- [x] 10.2 Write integration tests verifying `cluster="all"` fan-out works correctly for all tools with concurrent mock responses
- [x] 10.3 Write integration tests verifying structured error responses when all data sources are unavailable
- [x] 10.4 Verify all tool docstrings are accurate and suitable for LLM tool selection
- [x] 10.5 Run full test suite: `uv run pytest --cov --cov-report=term` — verify 90%+ coverage
- [x] 10.6 Run full lint suite: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/` — verify clean output
- [ ] 10.7 Verify dev container builds and all tests pass inside the container
- [ ] 10.8 Verify CI pipeline runs successfully end-to-end
- [x] 10.9 Write Claude Desktop MCP server configuration example in project README or inline comments
- [ ] 10.10 Collect team feedback and create backlog items for v2 scoping (M12)
