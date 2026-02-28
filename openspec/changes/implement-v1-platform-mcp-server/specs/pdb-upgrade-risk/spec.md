## ADDED Requirements

### Requirement: PDB Upgrade Risk Check

The server SHALL expose a `check_pdb_upgrade_risk` tool with two modes: `preflight` and `live`. The tool SHALL accept parameters: `cluster` (enum including `all`), `node_pool` (optional), and `mode` (enum: `preflight`, `live`; default `preflight`).

#### Scenario: Preflight mode invocation

- **WHEN** `check_pdb_upgrade_risk(cluster="prod-eastus", mode="preflight")` is invoked
- **THEN** the tool evaluates all PDBs cluster-wide and returns those that would block drain

#### Scenario: Live mode invocation

- **WHEN** `check_pdb_upgrade_risk(cluster="prod-eastus", mode="live")` is invoked during an active upgrade
- **THEN** the tool identifies PDBs currently blocking eviction on cordoned nodes

### Requirement: Preflight Mode Evaluation

In `preflight` mode, the tool SHALL evaluate all PodDisruptionBudgets across all namespaces and flag any where `maxUnavailable=0` or `minAvailable` equals the current ready replica count — indicating drain would be blocked if any pod were evicted.

#### Scenario: PDB with maxUnavailable=0 flagged

- **WHEN** a PDB has `maxUnavailable=0`
- **THEN** the tool flags it as a drain blocker with the workload name, namespace, current pod counts, and PDB rule

#### Scenario: PDB with minAvailable equal to ready count flagged

- **WHEN** a PDB has `minAvailable=3` and the workload has exactly 3 ready pods
- **THEN** the tool flags it as a drain blocker because any eviction would violate the PDB

#### Scenario: PDB with available disruption budget not flagged

- **WHEN** a PDB has `minAvailable=2` and the workload has 4 ready pods
- **THEN** the tool does not flag this PDB (2 disruptions allowed)

### Requirement: Preflight Node Pool Filtering

In `preflight` mode, when the `node_pool` parameter is provided, the tool SHALL filter evaluation to PDBs governing pods with replicas currently scheduled on the specified node pool. When `node_pool` is omitted, all PDBs cluster-wide SHALL be evaluated.

#### Scenario: Preflight filtered by node pool

- **WHEN** `check_pdb_upgrade_risk(cluster="prod-eastus", node_pool="user-pool", mode="preflight")` is invoked
- **THEN** only PDBs for pods running on `user-pool` nodes are evaluated

#### Scenario: Preflight without node pool filter

- **WHEN** `check_pdb_upgrade_risk(cluster="prod-eastus", mode="preflight")` is invoked without `node_pool`
- **THEN** all PDBs across all namespaces are evaluated

### Requirement: Live Mode Drain Blocker Detection

In `live` mode, the tool SHALL identify PDBs currently blocking eviction on cordoned nodes. The response SHALL include: PDB name, namespace, affected pods, affected nodes, and duration of the block. The tool SHALL supplement eviction event detection with direct PDB satisfiability evaluation to proactively detect blocks before the kubelet issues an eviction failure event.

#### Scenario: Live mode detects active PDB block

- **WHEN** a cordoned node has pods whose PDB evaluates to `disruptionsAllowed=0`
- **THEN** the response includes the PDB name, namespace, the pod pending eviction, the blocked node name, and the block duration

#### Scenario: Live mode with no active blocks

- **WHEN** no cordoned nodes have PDB-blocked evictions
- **THEN** the response indicates no active PDB blocks are detected

### Requirement: PDB Block Reference in Upgrade Progress

When a node is in `pdb_blocked` state in `get_upgrade_progress`, the output SHALL include a direct reference to the blocking PDB and a suggestion to run `check_pdb_upgrade_risk(mode="live")` for full detail.

#### Scenario: Cross-tool reference for PDB block

- **WHEN** `get_upgrade_progress` shows a node in `pdb_blocked` state
- **THEN** the output includes the PDB name and namespace and the message "Run check_pdb_upgrade_risk(mode='live') for full PDB block detail"
