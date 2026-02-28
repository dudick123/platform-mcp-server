# upgrade-progress Specification

## Purpose
TBD - created by archiving change implement-v1-platform-mcp-server. Update Purpose after archive.
## Requirements
### Requirement: Per-Node Upgrade Progress Tracking

The server SHALL expose a `get_upgrade_progress` tool that returns per-node upgrade state during in-flight upgrades. Each node SHALL be classified into one of six states: `upgraded`, `upgrading`, `cordoned`, `pdb_blocked`, `pending`, or `stalled`. The tool SHALL accept parameters: `cluster` (enum including `all`) and `node_pool` (optional, default: all upgrading pools).

#### Scenario: In-flight upgrade progress query

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked during an active upgrade
- **THEN** the response includes per-node: name, current state, node version, and time in current state; pool-level: nodes total, nodes upgraded, nodes remaining, elapsed duration, estimated remaining duration, and upgrade start timestamp

#### Scenario: No upgrade in progress

- **WHEN** `get_upgrade_progress(cluster="prod-eastus")` is invoked with no active upgrade
- **THEN** the response indicates no upgrade is in progress for that cluster

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

