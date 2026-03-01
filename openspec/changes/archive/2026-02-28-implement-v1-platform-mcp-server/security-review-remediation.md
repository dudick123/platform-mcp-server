# Security Review Remediation — Platform MCP Server

**Date:** 2026-02-28
**Scope:** Post-implementation security and best-practices review of all source code
**Total findings:** 12 (2 critical, 4 high, 4 medium, 2 informational)

---

## Status Summary

| Severity | Total | Fixed | Deferred | Rationale for Deferral |
|----------|-------|-------|----------|------------------------|
| Critical | 2 | 2 | 0 | — |
| High | 4 | 4 | 0 | — |
| Medium | 4 | 4 | 0 | — |
| Informational | 2 | 0 | 2 | Acknowledged; no code change required |

---

## Critical Findings (Fixed)

### CRIT-1: Blocking Synchronous SDK Calls in Async Context

- **Severity:** Critical
- **Files:** `clients/azure_aks.py`, `clients/k8s_core.py`, `clients/k8s_events.py`, `clients/k8s_metrics.py`, `clients/k8s_policy.py`
- **Issue:** Azure SDK and Kubernetes client calls are synchronous and block the event loop when called from async tool handlers. Under concurrent fan-out (`cluster="all"`), this serialises all I/O and can cause request timeouts.
- **Status:** Fixed
- **Fix:** Wrapped all synchronous SDK calls with `asyncio.to_thread()` to offload blocking I/O to the default thread pool executor. For `get_activity_log_upgrades`, extracted the full sync iteration (API call + lazy paginator loop) into a `_fetch_activity_logs` helper wrapped in a single `asyncio.to_thread()` call. `evaluate_pdb_satisfiability` was left unchanged as it is pure computation.

### CRIT-2: Thread-Unsafe Lazy Initialisation of SDK Clients

- **Severity:** Critical
- **Files:** `clients/azure_aks.py`, `clients/k8s_core.py`, `clients/k8s_events.py`, `clients/k8s_metrics.py`, `clients/k8s_policy.py`
- **Issue:** SDK client instances are lazily initialised on first use without thread-safety guards. If multiple concurrent requests trigger initialisation simultaneously, race conditions could produce duplicate clients or partially initialised state.
- **Status:** Fixed
- **Fix:** Added `threading.RLock()` to `AzureAksClient` (reentrant because `_get_container_client` and `_get_monitor_client` call `_get_credential` internally) and `threading.Lock()` to all four K8s clients. All `_get_api` / `_get_credential` / `_get_*_client` methods are now guarded with `with self._lock:`.

---

## High Severity Findings (Fixed)

### HIGH-1: IP Regex Matches Invalid Addresses

- **Severity:** High
- **File:** `src/platform_mcp_server/models.py`
- **Issue:** The `_IP_PATTERN` regex used `\d{1,3}` per octet, which matches syntactically invalid IPs like `999.999.999.999`. The scrubber would attempt to redact non-IP strings while missing the intent to only match valid IPv4 addresses.
- **Fix:** Replaced `\d{1,3}` with a proper octet alternation pattern `(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)` that validates each octet is in the range 0–255.
- **Tests added:** 5 tests covering valid IPs, invalid octets (999, 256), boundary (255, 0.0.0.0).

### HIGH-2: Insufficient `validate_cluster_config` Checks

- **Severity:** High
- **File:** `src/platform_mcp_server/config.py`
- **Issue:** Startup validation only checked for angle-bracket placeholder patterns in `subscription_id`. It did not validate UUID format, nor check that `resource_group`, `aks_cluster_name`, or `kubeconfig_context` were non-empty. This also left the door open for OData injection via malformed subscription IDs interpolated into Activity Log filter strings.
- **Fix:** Added UUID-format regex validation for `subscription_id` and non-empty checks for `resource_group`, `aks_cluster_name`, and `kubeconfig_context`. Error messages now enumerate all validation failures.
- **Tests added:** 4 tests covering invalid UUID, empty resource_group, empty aks_cluster_name, empty kubeconfig_context.

### HIGH-3: Missing Error Handling on `get_cluster_info()` in Upgrade Progress

- **Severity:** High
- **File:** `src/platform_mcp_server/tools/upgrade_progress.py`
- **Issue:** The `await aks_client.get_cluster_info()` call at the top of `get_upgrade_progress_handler` was unguarded. If the Azure API call failed, the entire handler crashed with an unhandled exception rather than returning a structured error response. This violated the graceful-degradation pattern used elsewhere in the codebase.
- **Fix:** Wrapped the call in try/except. On failure, returns an `UpgradeProgressOutput` with `upgrade_in_progress=False`, an error entry in the `errors` list, and a descriptive summary.
- **Tests added:** 1 async test verifying error output shape when `get_cluster_info` raises.

### HIGH-4: Incorrect PDB Attribution for Blocked Nodes

- **Severity:** High
- **File:** `src/platform_mcp_server/tools/upgrade_progress.py`
- **Issue:** When a node was classified as `pdb_blocked`, the code always attributed `blocker_list[0]` as the blocking PDB regardless of which node was being processed. In multi-node upgrades with multiple PDBs, this produced incorrect diagnostic output — a node could be reported as blocked by a PDB that affects pods on a completely different node.
- **Fix:** Now filters `blocker_list` to match entries whose `affected_nodes` list includes the current node. Falls back to `blocker_list[0]` only if no node-specific match is found, preserving backwards compatibility.
- **Tests:** Existing PDB classification tests cover the fallback path; the fix adds node-specific filtering that is exercised when `affected_nodes` data is present.

---

## Medium Severity Findings (Fixed)

### MED-1: No Error Handling in CPU/Memory Parsers

- **Severity:** Medium
- **File:** `src/platform_mcp_server/tools/node_pools.py`
- **Issue:** `_parse_cpu_millicores` and `_parse_memory_bytes` called `float()` on string values without try/except. Malformed resource values from the Kubernetes API (e.g., empty strings, non-numeric suffixes) would cause unhandled `ValueError` exceptions, crashing the entire node pool pressure check.
- **Fix:** Wrapped `float()` conversions in try/except `(ValueError, TypeError)`. On failure, returns `0.0` and logs a warning via structlog with the unparseable value for debugging.
- **Tests added:** 4 tests covering invalid CPU values (bare string, invalid millicore suffix) and invalid memory values (bare string, invalid binary suffix).

### MED-2: Duplicate Timestamp Parsing Logic

- **Severity:** Medium
- **Files:** `src/platform_mcp_server/tools/upgrade_progress.py`, `src/platform_mcp_server/tools/upgrade_metrics.py`
- **Issue:** Both modules contained identical timestamp parsing functions (`_parse_event_timestamp` and `_parse_ts`) with the same logic: parse ISO 8601 string, return `None` on failure. This duplication created maintenance risk — a bug fix in one copy could be missed in the other.
- **Fix:** Created `src/platform_mcp_server/utils.py` with a shared `parse_iso_timestamp` function. Both modules now alias their local names to the shared function (`_parse_event_timestamp = parse_iso_timestamp` and `_parse_ts = parse_iso_timestamp`), preserving API compatibility for existing tests and callers.
- **Tests added:** 4 tests for the shared utility covering valid timestamps, None input, empty strings, and invalid strings.

### MED-3: Unused `lookback_minutes` Parameter in Pod Health API

- **Severity:** Medium
- **Files:** `src/platform_mcp_server/models.py`, `src/platform_mcp_server/server.py`, `src/platform_mcp_server/tools/pod_health.py`, `README.md`
- **Issue:** The `lookback_minutes` parameter existed in `PodHealthInput`, was accepted by the MCP tool handler, passed through function signatures, and documented in the README — but was never applied to any query. No code path used it to filter pods by time. This non-functional parameter misleads LLM callers into believing time-based filtering works, potentially causing them to make incorrect assumptions about the data returned.
- **Fix:** Removed `lookback_minutes` from `PodHealthInput` model, `get_pod_health` tool handler, `get_pod_health_handler` and `get_pod_health_all` function signatures, and the README tools reference. Removed 4 associated boundary-value tests from `test_models.py`.
- **Tests updated:** Removed 4 lookback_minutes tests; updated 2 PodHealthInput tests that referenced the field.

### MED-4: OData Injection Risk via Config Values

- **Severity:** Medium
- **File:** `src/platform_mcp_server/clients/azure_aks.py`
- **Issue:** OData filter strings for Activity Log queries are constructed via f-string interpolation of `subscription_id` and other config values. If these values contained OData operators (e.g., `' or 1 eq 1`), the filter semantics could be altered.
- **Fix:** Mitigated by HIGH-2's UUID validation — `subscription_id` is now validated as a UUID at startup, and `resource_group`/`aks_cluster_name` are validated as non-empty. Valid UUIDs and typical Azure resource names cannot contain OData operators. No additional code change was needed beyond the config validation tightening.

---

## Informational Findings (Acknowledged)

### INFO-1: IP Regex May Match Version Strings

- **Severity:** Informational
- **Issue:** The IP scrubbing regex can match dotted-quad version strings like `1.29.8.0` that happen to look like valid IP addresses. With the tightened regex (HIGH-1), only syntactically valid octets (0–255) match, which reduces false positives but cannot eliminate them entirely without negative lookbehind for version contexts.
- **Status:** Acknowledged. Cosmetic risk only — version strings appearing as `[REDACTED_IP]` do not leak sensitive data. Documenting the limitation is sufficient.

### INFO-2: kubeconfig Mount Exposes All Contexts in Dev Container

- **Severity:** Informational
- **File:** `.devcontainer/devcontainer.json`
- **Issue:** The entire `~/.kube` directory is mounted read-only into the dev container, exposing kubeconfig contexts for clusters not in `CLUSTER_MAP`. A compromised container process could enumerate and potentially connect to unrelated clusters.
- **Status:** Acknowledged. The dev container is a local development convenience, not a production deployment vector. Risk is mitigated by the read-only mount and the server's read-only operational model.

---

## Verification

All fixes verified with:

| Check | Result |
|-------|--------|
| `uv run pytest --tb=short -q` | 282 passed |
| `uv run ruff check .` | All checks passed |
| `uv run mypy src/` | No issues found (20 source files) |
| `uv run pytest --cov --cov-report=term` | 99.04% coverage (≥ 90% required) |
