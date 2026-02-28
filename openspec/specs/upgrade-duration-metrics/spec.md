# upgrade-duration-metrics Specification

## Purpose
TBD - created by archiving change implement-v1-platform-mcp-server. Update Purpose after archive.
## Requirements
### Requirement: Upgrade Duration Metrics Query

The server SHALL expose a `get_upgrade_duration_metrics` tool that returns elapsed time, estimated remaining time, and historical upgrade durations per node pool. The tool SHALL accept parameters: `cluster` (enum including `all`), `node_pool` (str, required), and `history_count` (int, default 5).

#### Scenario: Duration metrics with history

- **WHEN** `get_upgrade_duration_metrics(cluster="prod-eastus", node_pool="user-pool", history_count=4)` is invoked
- **THEN** the response includes: current upgrade elapsed time and estimated remaining (if an upgrade is in progress), and the last 4 historical upgrade durations with mean, P90, min per node, and max per node

#### Scenario: No active upgrade

- **WHEN** no upgrade is in progress for the specified pool
- **THEN** the response includes only historical duration data

### Requirement: Current Run Timing from Events API

Per-node timing for the current in-progress upgrade SHALL be derived from Kubernetes `NodeUpgrade` and `NodeReady` event deltas via the Events API. The Events API has sufficient TTL for a single upgrade run.

#### Scenario: Current run per-node timing

- **WHEN** an upgrade is in progress and 5 nodes have completed
- **THEN** the response includes the per-node duration for each completed node, the mean seconds per node, and the slowest and fastest node in the current run

### Requirement: Historical Data from AKS Activity Log

Historical upgrade duration records SHALL be sourced from the AKS Activity Log (90-day retention) to avoid the 1-hour Kubernetes event TTL constraint.

#### Scenario: Historical durations retrieved

- **WHEN** `history_count=5` is requested
- **THEN** the response includes up to 5 past upgrade records from the AKS Activity Log, each with: date, version upgrade path, total duration, node count, min per-node time, and max per-node time

#### Scenario: Fewer historical records than requested

- **WHEN** only 2 past upgrades exist within the 90-day Activity Log retention
- **THEN** the response includes the 2 available records with a note that only 2 of 5 requested records were found

### Requirement: 60-Minute Anomaly Flag

The tool SHALL flag any estimated or elapsed total upgrade duration exceeding the configurable threshold (default 60 minutes) as potentially anomalous, with a note that ADO pipeline upgrades are expected to complete within that window.

#### Scenario: Estimated duration exceeds threshold

- **WHEN** the estimated total duration is 72 minutes
- **THEN** the response includes a flag: "Estimated duration (72m) exceeds the 60-minute expected baseline for ADO pipeline upgrades"

### Requirement: Statistical Summary

Historical data SHALL include mean duration, P90 duration, and per-pool statistics to support upgrade window sizing.

#### Scenario: Statistical summary included

- **WHEN** historical data for 5 past upgrades is returned
- **THEN** the response includes the mean duration, P90 duration, and whether all past upgrades completed within the 60-minute baseline

