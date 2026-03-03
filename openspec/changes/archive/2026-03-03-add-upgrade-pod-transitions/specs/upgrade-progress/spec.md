## ADDED Requirements

### Requirement: Pod Transition Summary During Upgrades

When an upgrade is in progress, the `get_upgrade_progress` tool SHALL include a `pod_transitions` section summarizing the pod-level impact of the upgrade. The summary SHALL include counts of pending and failed pods on nodes involved in the upgrade (cordoned, upgrading, or recently upgraded nodes), grouped by failure category consistent with the `get_pod_health` categories (scheduling, runtime, registry, config, unknown).

#### Scenario: Pods displaced by node drain during upgrade

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked during an active upgrade with cordoned nodes
- **THEN** the response includes a `pod_transitions` section with `pending_count`, `failed_count`, and a `by_category` dict grouping affected pods by failure reason (e.g., `{"scheduling": 5, "runtime": 1}`)

#### Scenario: No upgrade in progress

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked with no active upgrade
- **THEN** the `pod_transitions` field is `null`

#### Scenario: Upgrade in progress with no disrupted pods

- **WHEN** an upgrade is in progress but all evicted pods have been rescheduled successfully
- **THEN** the `pod_transitions` section shows `pending_count=0`, `failed_count=0`, and an empty `by_category` dict

### Requirement: Affected Pod Detail List

The `pod_transitions` section SHALL include a list of up to 20 affected pods providing per-pod detail: name, namespace, phase, failure reason, and the node the pod was evicted from or is pending on. This list SHALL be ordered by phase (Failed first, then Pending) to surface the most actionable items first.

#### Scenario: Affected pods listed with detail

- **WHEN** 8 pods are in Pending state on cordoned nodes during an upgrade
- **THEN** the `pod_transitions.affected_pods` list includes up to 20 pods, each with `name`, `namespace`, `phase`, `reason`, and `node_name`

#### Scenario: Affected pods capped at 20

- **WHEN** 35 pods are disrupted during an upgrade
- **THEN** the `pod_transitions.affected_pods` list includes the first 20 pods (Failed before Pending), and `pod_transitions.total_affected` reports 35

### Requirement: Upgrade-Scoped Pod Filtering

The pod transition summary SHALL only include pods on nodes that are part of the active upgrade (nodes in states: `cordoned`, `upgrading`, `pdb_blocked`, or `stalled`). Pods on `upgraded` or `pending` nodes SHALL be excluded to avoid counting pods on nodes not yet touched by the upgrade process.

#### Scenario: Pods filtered to upgrade-affected nodes only

- **WHEN** an upgrade has 3 cordoned nodes and 5 pending nodes
- **THEN** only pods on the 3 cordoned nodes are counted in `pod_transitions`; pods on the 5 pending (not-yet-cordoned) nodes are excluded

#### Scenario: Pod on upgraded node excluded

- **WHEN** a node has completed its upgrade (`upgraded` state) and its pods are running normally
- **THEN** pods on that node are not counted in `pod_transitions`

## MODIFIED Requirements

### Requirement: Per-Node Upgrade Progress Tracking

The server SHALL expose a `get_upgrade_progress` tool that returns per-node upgrade state during in-flight upgrades. Each node SHALL be classified into one of six states: `upgraded`, `upgrading`, `cordoned`, `pdb_blocked`, `pending`, or `stalled`. The tool SHALL accept parameters: `cluster` (enum including `all`) and `node_pool` (optional, default: all upgrading pools). When an upgrade is in progress, the response SHALL include a `pod_transitions` summary of pending and failed pods on upgrade-affected nodes.

#### Scenario: In-flight upgrade progress query

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked during an active upgrade
- **THEN** the response includes per-node: name, current state, node version, and time in current state; pool-level: nodes total, nodes upgraded, nodes remaining, elapsed duration, estimated remaining duration, and upgrade start timestamp; and a `pod_transitions` summary of disrupted pods

#### Scenario: No upgrade in progress

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked with no active upgrade
- **THEN** the response indicates no upgrade is in progress for that cluster and `pod_transitions` is `null`
