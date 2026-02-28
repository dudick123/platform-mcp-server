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

Six clusters spanning three environments and two Azure regions. 100+ tenants across all clusters.

| Cluster ID | Environment | Region |
|---|---|---|
| `dev-eastus` | dev | eastus |
| `dev-westus2` | dev | westus2 |
| `staging-eastus` | staging | eastus |
| `staging-westus2` | staging | westus2 |
| `prod-eastus` | prod | eastus |
| `prod-westus2` | prod | westus2 |

All tool `cluster` parameters use these composite IDs (plus `all` to query all six in parallel).

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
| Logging | `structlog` | Structured JSON logging to stderr |
| Transport | stdio (local only) | No network listener; credentials stay local |
| Linting & Formatting | Ruff | Replaces flake8, isort, black in a single tool |
| Type Checking | mypy (strict mode) | Static analysis; no implicit `Any` |
| Testing | pytest + pytest-asyncio + pytest-cov | Per-tool test files under `tests/` |
| Pre-commit | pre-commit | Runs Ruff + mypy before every commit |

### Package Management

`uv` is the sole package manager. No `pip`, `pip-tools`, or `requirements.txt`.

- **Lock file**: `uv.lock` is committed to the repo. All engineers and CI use the locked dependency set.
- **Adding a runtime dependency**: `uv add <package>`
- **Adding a dev dependency**: `uv add --group dev <package>`
- **Syncing the environment**: `uv sync` (installs from lock file)

Dependency groups in `pyproject.toml`:

```toml
[project]
dependencies = [
    "mcp[cli]",
    "kubernetes",
    "azure-mgmt-containerservice",
    "azure-identity",
    "pydantic>=2.0",
    "structlog",
]

[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "mypy",
    "ruff",
    "pre-commit",
]
```

---

## Project Layout

The project uses a `src/` layout (PEP 517/518) to prevent import ambiguity during testing.

```text
platform-mcp-server/
├── src/
│   └── platform_mcp_server/
│       ├── __init__.py
│       ├── server.py               # MCP entry point; tool registrations
│       ├── config.py               # Thresholds, cluster→region/resource-group mapping, kubeconfig context map
│       ├── models.py               # Pydantic models for all tool inputs/outputs
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── node_pools.py       # check_node_pool_pressure
│       │   ├── pod_health.py       # get_pod_health
│       │   ├── k8s_upgrades.py     # get_kubernetes_upgrade_status
│       │   ├── upgrade_progress.py # get_upgrade_progress (per-node state)
│       │   ├── upgrade_metrics.py  # get_upgrade_duration_metrics
│       │   └── pdb_check.py        # check_pdb_upgrade_risk (preflight + live)
│       └── clients/
│           ├── __init__.py
│           ├── k8s_core.py         # Kubernetes Core API wrapper (nodes, pods, namespaces)
│           ├── k8s_metrics.py      # Kubernetes Metrics API wrapper (CPU/memory usage)
│           ├── k8s_events.py       # Kubernetes Events API wrapper (NodeUpgrade, NodeReady)
│           ├── k8s_policy.py       # Kubernetes Policy API wrapper (PodDisruptionBudgets)
│           └── azure_aks.py        # AKS REST API wrapper (versions, upgrade profiles, activity log)
├── tests/
│   ├── conftest.py                 # Shared fixtures: mock K8s client, mock Azure client, cluster config
│   ├── fixtures/                   # Static test data (JSON responses, node lists, event payloads)
│   ├── test_node_pools.py
│   ├── test_pod_health.py
│   ├── test_k8s_upgrades.py
│   ├── test_upgrade_progress.py
│   ├── test_upgrade_metrics.py
│   ├── test_pdb_check.py
│   └── test_clients/
│       ├── conftest.py
│       ├── test_k8s_core.py
│       ├── test_k8s_metrics.py
│       ├── test_k8s_events.py
│       ├── test_k8s_policy.py
│       └── test_azure_aks.py
├── .devcontainer/
│   └── devcontainer.json           # Dev container configuration
├── .vscode/
│   ├── settings.json               # Workspace settings (formatter, linter, interpreter)
│   └── extensions.json             # Recommended extensions
├── openspec/
├── .pre-commit-config.yaml
├── uv.lock                         # Committed; deterministic dependency resolution
└── pyproject.toml
```

---

## Development Environments

This project supports two development flows. Both produce identical outcomes — the same linting, formatting, type checking, and test suite runs in both. Engineers choose based on their preference.

### Flow 1: Local IDE Development

For engineers who prefer running Python directly on their workstation.

#### Prerequisites

- Python 3.14+ installed
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `az login` completed (Azure CLI authenticated)
- `az aks get-credentials` run for all six clusters (merged kubeconfig)
- `KUBECONFIG` env var pointing to the merged kubeconfig

#### Setup

```bash
uv sync                       # install all dependencies from lock file
uv run pre-commit install     # install pre-commit hooks
```

#### VS Code

Committed workspace configuration lives in `.vscode/`. These files are checked into the repo so all engineers share identical IDE behavior.

`.vscode/settings.json`:

```json
{
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": "explicit",
      "source.organizeImports.ruff": "explicit"
    }
  },
  "python.analysis.typeCheckingMode": "strict",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["tests"],
  "mypy.runUsingActiveInterpreter": true
}
```

`.vscode/extensions.json`:

```json
{
  "recommendations": [
    "ms-python.python",
    "ms-python.mypy-type-checker",
    "charliermarsh.ruff",
    "ms-python.debugpy"
  ]
}
```

VS Code will automatically prompt engineers to install recommended extensions on first open.

#### PyCharm

- **Python interpreter**: point to the `uv`-managed virtual environment at `.venv/bin/python` (created by `uv sync`)
- **Ruff plugin**: install the [Ruff plugin](https://plugins.jetbrains.com/plugin/20574-ruff) and enable "Format on Save" and "Fix on Save"
- **mypy**: install the [mypy plugin](https://plugins.jetbrains.com/plugin/11086-mypy) or configure mypy as an External Tool (`uv run mypy src/`)
- **Test runner**: set pytest as the default test runner under Settings > Tools > Python Integrated Tools; point test directory to `tests/`
- **Source root**: mark `src/` as a Sources Root so imports resolve correctly

### Flow 2: Dev Container Development

For repeatable, isolated environments that don't depend on the engineer's local Python, Azure CLI, or kubectl installation. The dev container provides the full toolchain in a single container image.

#### Prerequisites

- Docker Desktop (or compatible container runtime) running
- VS Code with the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers), **or** a JetBrains IDE with [Dev Container support](https://www.jetbrains.com/help/idea/connect-to-devcontainer.html)

#### How to Start

1. Open the repo in VS Code
2. VS Code detects `.devcontainer/devcontainer.json` and prompts "Reopen in Container"
3. Click "Reopen in Container" — the container builds and the workspace opens inside it
4. All tools (`uv`, `ruff`, `mypy`, `pytest`, `az`, `kubectl`) are available immediately

#### Dev Container Configuration

`.devcontainer/devcontainer.json`:

```json
{
  "name": "Platform MCP Server",
  "image": "mcr.microsoft.com/devcontainers/python:3.14",
  "features": {
    "ghcr.io/devcontainers/features/azure-cli:1": {},
    "ghcr.io/devcontainers/features/kubectl-helm-minikube:1": {
      "kubectl": "latest",
      "helm": "none",
      "minikube": "none"
    }
  },
  "postCreateCommand": "curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH=\"$HOME/.local/bin:$PATH\" && uv sync && uv run pre-commit install",
  "customizations": {
    "vscode": {
      "settings": {
        "[python]": {
          "editor.defaultFormatter": "charliermarsh.ruff",
          "editor.formatOnSave": true,
          "editor.codeActionsOnSave": {
            "source.fixAll.ruff": "explicit",
            "source.organizeImports.ruff": "explicit"
          }
        },
        "python.analysis.typeCheckingMode": "strict",
        "python.testing.pytestEnabled": true,
        "python.testing.pytestArgs": ["tests"],
        "mypy.runUsingActiveInterpreter": true
      },
      "extensions": [
        "ms-python.python",
        "ms-python.mypy-type-checker",
        "charliermarsh.ruff",
        "ms-python.debugpy"
      ]
    }
  },
  "mounts": [
    "source=${localEnv:HOME}/.kube,target=/home/vscode/.kube,type=bind,readonly",
    "source=${localEnv:HOME}/.azure,target=/home/vscode/.azure,type=bind,readonly"
  ],
  "containerEnv": {
    "KUBECONFIG": "/home/vscode/.kube/config"
  }
}
```

#### What the Dev Container Provides

| Concern | How it's handled |
|---|---|
| Python 3.14 | Base image includes the correct version |
| `uv` | Installed by `postCreateCommand` |
| All project dependencies | `uv sync` runs automatically on container creation |
| Pre-commit hooks | Installed automatically by `postCreateCommand` |
| Azure CLI (`az`) | Installed via dev container feature; inherits host `~/.azure` credentials via bind mount |
| `kubectl` | Installed via dev container feature; inherits host `~/.kube/config` via bind mount |
| VS Code extensions | Ruff, mypy, Python, debugpy installed automatically inside the container |
| Linting on save | Configured via `customizations.vscode.settings` |

#### Credential Handling in Dev Containers

- **Azure credentials**: The host's `~/.azure` directory is mounted read-only into the container. The engineer's `az login` session from the host is reused — no need to re-authenticate inside the container.
- **Kubernetes credentials**: The host's `~/.kube` directory is mounted read-only. The merged kubeconfig from `az aks get-credentials` is available at the same path.
- **No credentials are baked into the container image.** Credentials are always bind-mounted from the host at runtime.

#### When to Use Which Flow

| Scenario | Recommended flow |
|---|---|
| Engineer has Python 3.14 and `az`/`kubectl` already installed | Local IDE |
| Engineer wants zero setup beyond Docker | Dev Container |
| Onboarding a new team member | Dev Container (fastest path to a working environment) |
| CI pipeline | Neither — CI uses its own `uv sync` workflow (see CI Pipeline section) |
| Debugging a workstation-specific issue | Local IDE (direct access to host network and credentials) |

---

## Project Conventions

### Code Style

This project follows established Python Enhancement Proposals (PEPs) as the foundation for all code conventions.

- **PEP 8** — all code follows PEP 8 style. Enforced automatically by Ruff (see Linting & Formatting below).
- **PEP 257** — Google-style docstrings for all public modules, classes, and functions. Tool docstrings are additionally used by the LLM for tool selection; they must accurately describe when and how to invoke the tool.
- **PEP 484 / 526** — type hints required on all public functions, method signatures, and class attributes. Use `X | None` union syntax (PEP 604) instead of `Optional[X]`. Use `TypeAlias` (PEP 613) for complex type definitions.
- **PEP 621** — all project metadata, dependencies, and tool configuration live in `pyproject.toml`. No `setup.py`, `setup.cfg`, or `requirements.txt`.
- Pydantic v2 models for **all** tool inputs and outputs — no raw dicts in tool signatures
- `config.py` is the single source of truth for thresholds, cluster mappings, and kubeconfig context names — no hardcoded values in tool modules

### Linting, Formatting & Type Checking

All quality tools are configured in `pyproject.toml` — no separate config files.

#### Ruff (linter + formatter)

Ruff replaces flake8, isort, black, pyflakes, and pycodestyle as a single, fast tool.

- **Linting**: `uv run ruff check .` — runs all enabled rule sets
- **Formatting**: `uv run ruff format .` — deterministic formatting (replaces black)
- **Auto-fix**: `uv run ruff check --fix .` — auto-fixes safe lint violations

Minimum enabled rule sets in `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py314"
line-length = 120

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "UP",   # pyupgrade
    "B",    # flake8-bugbear
    "SIM",  # flake8-simplify
    "RUF",  # Ruff-specific rules
]

[tool.ruff.lint.isort]
known-first-party = ["platform_mcp_server"]
```

#### mypy (static type checking)

- **Run**: `uv run mypy src/`
- **Strict mode** enabled — all functions must have type annotations; no implicit `Any`

Minimum configuration in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.14"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true

[[tool.mypy.overrides]]
module = ["kubernetes.*", "azure.*"]
ignore_missing_imports = true
```

#### Pre-commit Hooks

Pre-commit runs Ruff and mypy automatically before every commit. The `.pre-commit-config.yaml` file is committed to the repo.

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.15.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic>=2.0]
```

Install: `uv run pre-commit install`

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

All logging uses `structlog` configured for JSON output to `stderr`. Do not use the stdlib `logging` module directly.

- Structured JSON to `stderr` (MCP clients forward stderr to their log facilities)
- Each tool invocation logs: tool name, parameters, data source latency, success/failure
- Use `structlog.get_logger()` in every module — never `print()` or `logging.getLogger()`
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

**Minimum coverage target: 90% line coverage.** CI fails if coverage drops below this threshold.

Every tool module must have tests covering:

- Happy path with realistic fixture data
- Graceful degradation when a backing API is unavailable
- Pressure/state threshold boundaries (e.g., exactly at `warning` vs. `critical` cutoffs)
- `cluster="all"` parallel fan-out behavior
- Structured error output format (not raw exceptions)

#### Pytest Plugins

- **pytest-asyncio** — required for testing `asyncio`-based parallel cluster queries. Use `@pytest.mark.asyncio` on async test functions.
- **pytest-cov** — coverage reporting integrated into the test run.

Configuration in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.coverage.run]
source = ["src/platform_mcp_server"]
branch = true

[tool.coverage.report]
fail_under = 90
show_missing = true
exclude_lines = ["if TYPE_CHECKING:", "pragma: no cover"]
```

#### Test Fixtures and Shared Mocks

- **`tests/conftest.py`** — shared fixtures for all test modules: mock Kubernetes CoreV1Api client, mock Azure ContainerServiceClient, test cluster config mapping, and a pre-configured `structlog` test logger.
- **`tests/fixtures/`** — static JSON files representing realistic API responses (node lists, pod lists, event payloads, AKS upgrade profiles). Fixtures are loaded by helpers in `conftest.py`.
- **`tests/test_clients/conftest.py`** — client-specific fixtures (raw API response objects, error responses).
- Use **factory functions** in `conftest.py` for parameterized test data (e.g., `make_node(pool="user-pool", version="1.29.8", unschedulable=True)`).

#### Running Tests

```bash
uv run pytest                          # full suite with coverage
uv run pytest tests/test_node_pools.py # single module
uv run pytest --tb=short               # compact failure output
uv run pytest --cov --cov-report=term  # coverage summary to terminal
```

### Git Workflow

**AI agents must not perform any git operations.** This includes — but is not limited to — creating branches, committing, pushing, pulling, rebasing, merging, tagging, or interacting with any remote. All git operations are the exclusive responsibility of the human engineer.

- Branch from `main`; PR required for all changes
- Commit messages: conventional commits style (`feat:`, `fix:`, `refactor:`, `test:`, `chore:`)
- OpenSpec proposals required before implementing new tools or breaking changes (see `openspec/AGENTS.md`)
- The engineer creates the branch before starting work and pushes when ready — the agent only reads and writes files

### CI Pipeline

CI runs on every pull request and must pass before merge. The pipeline uses Azure DevOps Pipelines (consistent with the team's existing ADO infrastructure).

#### PR Pipeline Steps

Every PR triggers the following stages in order:

1. **Lint** — `uv run ruff check .` — fails the build on any violation
2. **Format check** — `uv run ruff format --check .` — fails if any file would be reformatted
3. **Type check** — `uv run mypy src/` — fails on any type error (strict mode)
4. **Test** — `uv run pytest --cov --cov-report=xml` — fails if any test fails or coverage drops below 90%

All four stages must pass. There is no manual override.

#### Branch Protection Rules

- `main` branch is protected: direct pushes are blocked
- PRs require at least one approval before merge
- PRs require all CI checks to pass before merge
- Stale approvals are dismissed on new pushes

#### CI Configuration

```yaml
# azure-pipelines.yml (minimal)
trigger:
  branches:
    include: [main]

pr:
  branches:
    include: [main]

pool:
  vmImage: "ubuntu-latest"

steps:
  - task: UseUv@0
  - script: uv sync
    displayName: "Install dependencies"
  - script: uv run ruff check .
    displayName: "Lint"
  - script: uv run ruff format --check .
    displayName: "Format check"
  - script: uv run mypy src/
    displayName: "Type check"
  - script: uv run pytest --cov --cov-report=xml --cov-report=term
    displayName: "Test + coverage"
```

#### Local Quality Loop

Engineers should run the same checks locally before pushing:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest
```

Pre-commit hooks (see Linting & Formatting above) catch lint and type errors automatically at commit time, so most issues are caught before the push.

---

## Domain Context

- **GitOps platform**: ArgoCD / Akuity SaaS for application sync; Azure DevOps pipelines + Terraform for AKS upgrade orchestration
- **Multi-region topology**: Six AKS clusters across two regions (eastus, westus2) and three environments (dev, staging, prod). See Target Clusters table above for the full list.
- **Upgrade cadence**: Upgrades are performed **one region and one environment at a time**. The wave order is region-first: `eastus(dev → staging → prod)` then `westus2(dev → staging → prod)`. ADO pipeline upgrades are expected to complete within **60 minutes** per cluster under normal conditions — this is the anomaly threshold.
- **Historical upgrade data**: Sourced from AKS Activity Log (90-day retention), not Kubernetes Events API (1-hour TTL). Events API is used only for current in-progress run timing.
- **Node pool identification**: Nodes are grouped by the `agentpool` label (fallback: `kubernetes.azure.com/agentpool`)
- **Multi-cluster kubeconfig**: Engineers run `az aks get-credentials` for all six clusters; the server resolves contexts from `config.py` — a single merged kubeconfig is the expected setup

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
