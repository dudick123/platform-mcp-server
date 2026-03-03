# Change: Add pod transition summary to upgrade progress

## Why

During AKS node pool upgrades, nodes are cordoned and drained, causing pod evictions that result in transient Pending and Failed states. Operators need to see the pod-level impact of an upgrade alongside node-level progress to distinguish upgrade-induced pod disruptions from unrelated failures and to identify pods that are stuck post-eviction.

## What Changes

- Extend `UpgradeProgressOutput` with a `pod_transitions` section summarizing pod states on upgrading/cordoned nodes
- Add per-pool counts of pending, failed, and eviction-related pods during active upgrades
- Group affected pods by failure category (scheduling, runtime, registry, config) consistent with existing `get_pod_health` categories
- Include top affected pods (capped) with namespace, phase, reason, and owning node
- Return empty `pod_transitions` when no upgrade is in progress (no behavioral change to existing fields)

## Impact

- Affected specs: `upgrade-progress`
- Affected code: `tools/upgrade_progress.py`, `models.py` (new Pydantic models), `tests/test_upgrade_progress.py`
- No breaking changes: new optional fields added to existing output model
- Reuses existing `K8sCoreClient.get_pods()` — no new client methods needed
