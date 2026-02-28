# Code Review — v1 Platform MCP Server

## Critical Issues

### 1. `asyncio.gather(return_exceptions=False)` crashes entire fan-out on single cluster failure
**Files:** All six `_all()` functions in `tools/*.py`

All fan-out functions use `return_exceptions=False`. If any one of 6 clusters raises (network error, auth failure, cluster down), the entire call raises and the caller gets no data for the other 5 healthy clusters. The per-cluster handlers have internal `ToolError` lists for graceful degradation, but this is bypassed in the fan-out path.

**Fix:** Use `return_exceptions=True` and filter results.

### 2. Pending pod count is cluster-wide but applied per-pool
**File:** `tools/node_pools.py:107,133`

`pending_count = len(pods)` gets the cluster-wide total of pending pods. This same count is then passed to `_classify_pressure()` for every pool, inflating pressure levels. A cluster with 2 pending pods reports both `systempool` and `userpool` at the same elevated pending pressure.

**Fix:** Filter pending pods per-pool using node affinity/selector labels, or pass 0 and document pending pods as cluster-wide in the summary.

### 3. PDB blocker classification is cluster-wide, not per-node
**File:** `tools/upgrade_progress.py:57,133`

`pdb_blocker_names` is a set of all blocking PDB names cluster-wide. `_classify_node_state` checks `if unschedulable and pdb_blockers` — just truthiness of the set. If *any* PDB blocks anywhere, *every* cordoned node is classified as `pdb_blocked` regardless of whether that PDB's pods run on that node.

**Fix:** Cross-reference PDB selectors against pods on each node, or at minimum pass per-node blocker info.

---

## Important Issues

### 4. Missing `.gitignore` — `__pycache__/` and `.coverage` committed
**File:** Project root (missing)

No root `.gitignore`. Binary `.coverage` file and `__pycache__/` directories are tracked by git.

### 5. `azure-pipelines.yml` uses `UseUv@0` marketplace task without fallback
**File:** `azure-pipelines.yml:13`

`UseUv@0` requires the [astral-sh/setup-uv](https://marketplace.visualstudio.com/items?itemName=astral-sh.setup-uv) extension. Without it, the pipeline fails silently. No fallback or documentation.

**Fix:** Replace with inline install script.

### 6. `load_kube_config()` mutates global SDK state — concurrent fan-out races
**Files:** `clients/k8s_metrics.py:25`, `clients/k8s_events.py:26`, `clients/k8s_policy.py:25`

`load_kube_config()` sets the global default K8s configuration. When 6 clusters run concurrently via `asyncio.gather`, the last `load_kube_config()` call wins and all subsequent API calls use that cluster's context. `k8s_core.py` already uses the correct pattern (`Configuration.get_default_copy()`), but the other 3 clients have a race window.

**Fix:** Use `new_client_from_config(context=...)` which returns an isolated `ApiClient` without mutating globals.

### 7. `is_upgrading` false-negative when `current_orchestrator_version` is `None`
**File:** `tools/k8s_upgrades.py:68-70`

When `current_orchestrator_version` is `None` mid-upgrade, `azure_aks.py` falls back to `orchestrator_version` (the target), making `current_version == target_version` → `is_upgrading=False`. Wrong.

**Fix:** Guard the version comparison with `None` checks.

### 8. `requires-python = ">=3.14"` blocks all stable Python
**File:** `pyproject.toml:6`

Python 3.14 is pre-release. No 3.14-specific syntax is used (`from __future__ import annotations` handles union types). This blocks installation on 3.11/3.12/3.13.

**Fix:** Change to `>=3.11`.

### 9. `mypy` pre-commit hook missing dependency stubs
**File:** `.pre-commit-config.yaml:12`

The mypy hook only installs `pydantic`. Missing `kubernetes`, `azure-*`, `structlog` stubs means pre-commit mypy silently skips type-checking most of the codebase.

**Fix:** Add missing dependencies or accept partial checking and document.

### 10. `elapsed_seconds` is sum of node durations, not wall-clock time
**File:** `tools/upgrade_metrics.py:71-73,141`

`total_elapsed = sum(durations)` sums per-node durations (e.g., 3 nodes × 5min = 15min), but this is stored as `elapsed_seconds` and compared against the 60-minute baseline as wall-clock. Causes incorrect anomaly flagging.

**Fix:** Use `(now - earliest_upgrade_event).total_seconds()` for true wall-clock elapsed.
