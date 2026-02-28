## ADDED Requirements

### Requirement: Node Pool Pressure Query

The server SHALL expose a `check_node_pool_pressure` tool that returns per-node-pool CPU request ratios, memory request ratios, pending pod counts, ready node counts, max node counts (from cluster-autoscaler annotation), and a pressure level for each pool. The tool SHALL accept a `cluster` parameter with valid values `dev-eastus`, `dev-westus2`, `staging-eastus`, `staging-westus2`, `prod-eastus`, `prod-westus2`, or `all`.

#### Scenario: Single cluster pressure query

- **WHEN** `check_node_pool_pressure(cluster="prod-eastus")` is invoked
- **THEN** the tool returns a list of node pools in `prod-eastus` with CPU requests % of allocatable, memory requests % of allocatable, pending pod count, ready node count, max node count, and pressure level per pool

#### Scenario: All-cluster parallel query

- **WHEN** `check_node_pool_pressure(cluster="all")` is invoked
- **THEN** the tool queries all six clusters concurrently and returns combined results grouped by cluster

### Requirement: Pressure Level Classification

Each node pool SHALL be assigned a pressure level (`ok`, `warning`, `critical`) based on configurable thresholds. The pool's overall level SHALL be the highest severity across CPU, memory, and pending pod metrics.

#### Scenario: Critical pressure detected

- **WHEN** a node pool has CPU requests at 91% of allocatable (≥90% threshold)
- **THEN** the pool is classified as `critical` regardless of memory and pending pod levels

#### Scenario: Warning from pending pods

- **WHEN** a node pool has 3 pending pods (>0 threshold) but CPU at 60% and memory at 70%
- **THEN** the pool is classified as `warning` because pending pods exceed the ok threshold

#### Scenario: OK when all metrics below thresholds

- **WHEN** a node pool has CPU at 50%, memory at 60%, and 0 pending pods
- **THEN** the pool is classified as `ok`

### Requirement: Node Pool Identification

Nodes SHALL be grouped into pools using the `agentpool` node label. If `agentpool` is not present, the tool SHALL fall back to the `kubernetes.azure.com/agentpool` label. If neither label is found, the tool SHALL surface a warning in the output.

#### Scenario: Nodes grouped by agentpool label

- **WHEN** nodes have the `agentpool` label set
- **THEN** the tool groups nodes by that label value to form per-pool aggregations

#### Scenario: Fallback to kubernetes.azure.com/agentpool

- **WHEN** a node lacks the `agentpool` label but has `kubernetes.azure.com/agentpool`
- **THEN** the tool uses the fallback label for grouping

### Requirement: Graceful Degradation Without Metrics API

If the Kubernetes Metrics API (`metrics-server`) is unavailable, the tool SHALL return available data from the Core API (node allocatable capacity, pending pod counts) with a structured error note indicating that utilization metrics are unavailable. The tool SHALL NOT crash or return an empty response.

#### Scenario: Metrics API unavailable

- **WHEN** `check_node_pool_pressure` is invoked and the Metrics API returns an error
- **THEN** the response includes node count, pending pod count, and max node count from the Core API, plus a `ToolError` with `source="metrics-server"` and `partial_data=true`

### Requirement: Human-Readable Summary

Every `check_node_pool_pressure` response SHALL include a human-readable summary line suitable for LLM context, along with a data timestamp indicating freshness.

#### Scenario: Summary line included

- **WHEN** the tool returns results
- **THEN** the response includes a summary line (e.g., "2 of 4 node pools in prod-eastus are under pressure") and a UTC timestamp
