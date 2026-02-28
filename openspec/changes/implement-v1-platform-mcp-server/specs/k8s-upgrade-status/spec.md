## ADDED Requirements

### Requirement: Kubernetes Version and Upgrade Status Query

The server SHALL expose a `get_kubernetes_upgrade_status` tool that returns the control plane version, per-node-pool version, available upgrades, and support status for each cluster. The tool SHALL accept a `cluster` parameter with valid values including `all` for parallel fleet-wide queries.

#### Scenario: Single cluster version query

- **WHEN** `get_kubernetes_upgrade_status(cluster="prod-eastus")` is invoked
- **THEN** the tool returns the control plane version, each node pool's current version, available upgrade versions, and Microsoft support status per version

#### Scenario: Fleet-wide version query

- **WHEN** `get_kubernetes_upgrade_status(cluster="all")` is invoked
- **THEN** the tool queries all six clusters concurrently and returns a consolidated version table

### Requirement: Active Upgrade State Detection

The tool SHALL surface whether an upgrade is currently in progress per cluster and node pool. When an upgrade is active, the response SHALL include which node pool is upgrading and the target version.

#### Scenario: Upgrade in progress detected

- **WHEN** a cluster has an active node pool upgrade running
- **THEN** the response includes `upgrade_active=true`, the upgrading node pool name, the current version, and the target version

#### Scenario: No upgrade in progress

- **WHEN** no cluster has an active upgrade
- **THEN** the response includes `upgrade_active=false` for each cluster

### Requirement: Deprecated Version Flagging

The tool SHALL flag node pools on Kubernetes versions at or past Microsoft's end-of-support date, or within 60 days of end-of-support.

#### Scenario: Version past end-of-support

- **WHEN** a node pool is running a version whose support has ended
- **THEN** the response marks it with status `deprecated` and includes the end-of-support date

#### Scenario: Version approaching end-of-support

- **WHEN** a node pool is running a version within 60 days of end-of-support
- **THEN** the response marks it with a warning and includes the days remaining

### Requirement: Partial Data on API Failure

If the AKS API returns partial data (e.g., one cluster unavailable), the tool SHALL return available data with a clear indication of what is missing via the `ToolError` model with `partial_data=true`.

#### Scenario: One cluster unavailable

- **WHEN** `get_kubernetes_upgrade_status(cluster="all")` is invoked and the AKS API for `staging-westus2` is unreachable
- **THEN** the response includes data for the other five clusters and a `ToolError` entry for `staging-westus2` with `source="aks-api"` and `partial_data=true`
