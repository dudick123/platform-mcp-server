# upgrade-progress Specification

## Purpose
TBD - created by archiving change implement-v1-platform-mcp-server. Update Purpose after archive.
## Requirements
### Requirement: Per-Node Upgrade Progress Tracking

The server SHALL expose a `get_upgrade_progress` tool that returns per-node upgrade state during in-flight upgrades. Each node SHALL be classified into one of six states: `upgraded`, `upgrading`, `cordoned`, `pdb_blocked`, `pending`, or `stalled`. The tool SHALL accept parameters: `cluster` (enum including `all`) and `node_pool` (optional, default: all upgrading pools). When an upgrade is in progress, the response SHALL include a `pod_transitions` summary of pending and failed pods on upgrade-affected nodes.

#### Scenario: In-flight upgrade progress query

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked during an active upgrade
- **THEN** the response includes per-node: name, current state, node version, and time in current state; pool-level: nodes total, nodes upgraded, nodes remaining, elapsed duration, estimated remaining duration, and upgrade start timestamp; and a `pod_transitions` summary of disrupted pods

#### Scenario: No upgrade in progress

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked with no active upgrade
- **THEN** the response indicates no upgrade is in progress for that cluster and `pod_transitions` is `null`

### Requirement: Node State Classification

Node states SHALL be derived from Kubernetes events and node conditions as follows:

- `upgraded` — `NodeReady` event after `NodeUpgrade`; version matches target
- `upgrading` — `NodeUpgrade` event present; `NodeReady` not yet seen
- `cordoned` — Node `spec.unschedulable=true`; no `NodeUpgrade` event yet
- `pdb_blocked` — Cordoned node with PDB `disruptionsAllowed=0` on affected pods
- `pending` — Pool is upgrading; node shows old version; not yet cordoned
- `stalled` — Pool upgrade has exceeded 60-minute threshold; node not yet `NodeReady`; no PDB block detected

#### Scenario: Node classified as upgrading

- **WHEN** a node has a `NodeUpgrade` event but no subsequent `NodeReady` event
- **THEN** the node state is `upgrading`

#### Scenario: Node classified as pdb_blocked

- **WHEN** a cordoned node has pods whose PDB evaluates to `disruptionsAllowed=0`
- **THEN** the node state is `pdb_blocked` and the response includes a reference to the blocking PDB and a suggestion to run `check_pdb_upgrade_risk(mode="live")`

#### Scenario: Node classified as stalled

- **WHEN** the total pool upgrade has exceeded 60 minutes, a node is not yet `NodeReady`, and no PDB block is detected
- **THEN** the node state is `stalled`

### Requirement: Duration Estimation

The tool SHALL calculate estimated remaining time as `mean_seconds_per_node_so_far × nodes_remaining`, derived from `NodeUpgrade`/`NodeReady` event deltas for completed nodes in the current run.

#### Scenario: Estimated remaining time calculated

- **WHEN** 5 of 12 nodes have completed upgrade with a mean of 5 minutes per node
- **THEN** the estimated remaining time is approximately 35 minutes (7 remaining × 5 minutes)

### Requirement: Anomaly Flagging

The tool SHALL flag any estimated or elapsed total upgrade duration exceeding the configurable threshold (default 60 minutes) as potentially anomalous. The flag SHALL be suppressed when the cause is a known PDB block.

#### Scenario: Duration exceeds threshold without PDB block

- **WHEN** an upgrade has been running for 75 minutes with no PDB blocks detected
- **THEN** the response includes an anomaly flag noting the 60-minute expected baseline is exceeded

#### Scenario: Duration exceeds threshold with PDB block

- **WHEN** an upgrade has been running for 75 minutes and a PDB block is identified
- **THEN** the anomaly flag is suppressed; the response notes the delay is caused by a PDB block

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

