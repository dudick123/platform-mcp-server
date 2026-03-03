## 1. Models

- [x] 1.1 Add `AffectedPod` model (name, namespace, phase, reason, node_name)
- [x] 1.2 Add `PodTransitionSummary` model (pending_count, failed_count, by_category dict, affected_pods list, total_affected int)
- [x] 1.3 Add optional `pod_transitions: PodTransitionSummary | None` field to `UpgradeProgressOutput`
- [x] 1.4 Write model unit tests (serialization, defaults, null when no upgrade)

## 2. Handler Logic

- [x] 2.1 Write tests for pod transition collection during active upgrade (TDD red)
- [x] 2.2 Write tests for empty pod transitions when no disrupted pods
- [x] 2.3 Write tests for pod filtering to upgrade-affected nodes only
- [x] 2.4 Write tests for affected pod cap at 20 with ordering (Failed first)
- [x] 2.5 Implement pod transition collection in `get_upgrade_progress_handler`: query pods on cordoned/upgrading/pdb_blocked/stalled nodes, classify by failure category, build summary
- [x] 2.6 Reuse `_categorize_failure` and `_is_unhealthy` from `pod_health.py` by extracting to a shared module or importing directly

## 3. Integration

- [x] 3.1 Update server tool integration tests for new `pod_transitions` field
- [x] 3.2 Verify fan-out (`cluster="all"`) includes pod transitions per cluster
- [x] 3.3 Run full quality suite (ruff, mypy, pytest --cov) — ensure 90% coverage maintained

## 4. Validation

- [x] 4.1 Run `openspec validate add-upgrade-pod-transitions --strict --no-interactive`
