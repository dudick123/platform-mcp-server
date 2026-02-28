# Security Review — Platform MCP Server

**Date:** 2026-02-28
**Reviewer:** AI Security Engineer
**Scope:** All source code, tests, configuration, and CI pipeline

---

## Critical Findings

### CRIT-1: `history_count` Parameter Has No Upper-Bound — DoS Vector
- **File:** `server.py:149`, `models.py`, `azure_aks.py:163`
- **Issue:** `history_count` accepted without upper bound. Large value causes full Activity Log stream enumeration (unbounded memory/time/API quota).
- **Fix:** Add `Field(ge=1, le=50)` to model; hard cap in `azure_aks.py`.

### CRIT-2: `lookback_minutes` Parameter Has No Bounds — DoS Vector
- **File:** `server.py:62`, `models.py:107`
- **Issue:** `lookback_minutes` accepted without ge/le constraint. Negative or huge values pass validation.
- **Fix:** Add `Field(ge=1, le=1440)` to `PodHealthInput.lookback_minutes`.

---

## High Severity Findings

### HIGH-1: Unscrubbed Exception Messages Re-raised to MCP Caller
- **File:** `server.py:56-58` (all 6 tool handlers)
- **Issue:** Raw exceptions from Azure/K8s SDKs contain subscription IDs, resource URIs, and IPs. These bypass `scrub_sensitive_values()` when re-raised.
- **Fix:** Wrap exceptions in sanitised `RuntimeError` before re-raising.

### HIGH-2: `mode` Parameter Silently Accepts Invalid Values
- **File:** `server.py:177`, `pdb_check.py:19-22`
- **Issue:** Any value other than `"live"` silently falls through to preflight mode. No error for `mode="LIVE"` or `mode="debug"`.
- **Fix:** Validate mode explicitly; raise `ValueError` for unrecognised values.

### HIGH-3: `namespace` Parameter Unsanitised Before K8s API Call
- **File:** `k8s_core.py:93`, `k8s_events.py:79`
- **Issue:** Namespace passed directly to K8s API without RFC 1123 validation. Malformed values with `/`, `?`, `#` could interfere with HTTP path.
- **Fix:** Validate against RFC 1123 label regex before use.

### HIGH-4: `node_pool` Parameter Unconstrained
- **File:** `upgrade_progress.py:97`, `upgrade_metrics.py:183`
- **Issue:** `node_pool` not validated against AKS naming rules (lowercase alphanumeric, max 12 chars). Arbitrary text passes through to output.
- **Fix:** Validate against `^[a-z][a-z0-9]{0,11}$`.

### HIGH-5: Placeholder Subscription IDs Not Validated at Startup
- **File:** `config.py:63-107`
- **Issue:** `<dev-subscription-id>` etc. used as real config. No runtime check that placeholders were replaced. Silent Azure API failures.
- **Fix:** Add startup validation that rejects placeholder patterns.

---

## Medium Severity Findings

### MED-1: Activity Log Filter Built via f-string Interpolation
- **File:** `azure_aks.py:185-190`
- **Issue:** OData filter string constructed by interpolating config values. If config becomes dynamic, injection possible.
- **Fix:** Validate config values match expected patterns before interpolation.

### MED-2: IP Scrubbing Regex False-Positives on Version Strings
- **File:** `models.py:35,47`
- **Issue:** Regex matches dotted-quad version strings like `1.29.8.0`. Mostly cosmetic risk.
- **Fix:** Acceptable risk; document the limitation.

### MED-3: kubeconfig Mount Exposes All Contexts in Devcontainer
- **File:** `devcontainer.json:38`
- **Issue:** Entire `~/.kube` mounted. Container can access clusters not in CLUSTER_MAP.
- **Fix:** Document the risk. No code change needed for production.

### MED-4: Cluster FQDNs Not Covered by Scrubbing Patterns
- **File:** `server.py:50-53`, `models.py`
- **Issue:** `*.azmk8s.io` hostnames not scrubbed. Currently not in output models but could leak via error paths.
- **Fix:** Add FQDN pattern to `scrub_sensitive_values`.

### MED-5: Exception Type Names Leaked in ToolError Messages
- **File:** `k8s_upgrades.py:29-36`
- **Issue:** `type(e).__name__` included in ToolError, leaking SDK internal class names.
- **Fix:** Use generic error strings without exception type names.

---

## Low Severity Findings

### LOW-1: CI `curl | sh` Without Hash Pinning
- **File:** `azure-pipelines.yml:13`
- **Issue:** `uv` installed via unpinned `curl | sh`. Supply chain risk.
- **Fix:** Pin to specific version with hash verification.

### LOW-2: No Lockfile in Repository
- **File:** `pyproject.toml:11-18`
- **Issue:** No `uv.lock` committed. Non-reproducible builds.
- **Fix:** Commit lockfile; use `uv sync --frozen` in CI.

### LOW-3: No Security Scanners in Pre-commit/CI
- **File:** `.pre-commit-config.yaml`
- **Issue:** No `bandit` or `pip-audit` configured.
- **Fix:** Add security scanning tools.

### LOW-4: Scrubber Missing Tenant IDs, Vault Names, FQDNs
- **File:** `models.py:35-50`
- **Issue:** UUID tenant IDs and `*.vault.azure.net` hostnames not covered.
- **Fix:** Extend scrubbing patterns.

---

## Implementation Plan

Implement fixes for CRIT-1, CRIT-2, HIGH-1 through HIGH-5, MED-4, MED-5, LOW-1, and LOW-3.
Defer MED-1 (config is static), MED-2 (cosmetic), MED-3 (devcontainer only), LOW-2 (requires `uv lock`), LOW-4 (extend with MED-4).
