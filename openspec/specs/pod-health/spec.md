# pod-health Specification

## Purpose
TBD - created by archiving change implement-v1-platform-mcp-server. Update Purpose after archive.
## Requirements
### Requirement: Pod Health Diagnostics Query

The server SHALL expose a `get_pod_health` tool that returns pending and failed pods with failure reason, restart count, and the most recent Kubernetes event message per pod. The tool SHALL accept parameters: `cluster` (enum including `all`), `namespace` (optional, default all), `status_filter` (enum: `pending`, `failed`, `all`; default `all`), and `lookback_minutes` (int, default 30).

#### Scenario: Query all failing pods in a cluster

- **WHEN** `get_pod_health(cluster="prod-eastus")` is invoked with defaults
- **THEN** the tool returns all pods currently in an unhealthy state plus any pods that transitioned through a failure state within the last 30 minutes

#### Scenario: Namespace-scoped query

- **WHEN** `get_pod_health(cluster="prod-eastus", namespace="payments")` is invoked
- **THEN** the tool returns only pods in the `payments` namespace

#### Scenario: Status filter for pending pods only

- **WHEN** `get_pod_health(cluster="prod-eastus", status_filter="pending")` is invoked
- **THEN** only pods in `Pending` phase are returned

### Requirement: Lookback Semantics

The `lookback_minutes` parameter SHALL filter resolved or transient failures by event time. Pods that are currently in an unhealthy state (Pending, CrashLoopBackOff, ImagePullBackOff, OOMKilled, etc.) SHALL always be included regardless of pod age.

#### Scenario: Currently unhealthy pod included regardless of age

- **WHEN** a pod has been in `Pending` state for 3 hours and `lookback_minutes=30`
- **THEN** the pod is included in results because it is currently unhealthy

#### Scenario: Resolved failure excluded outside lookback window

- **WHEN** a pod was in `CrashLoopBackOff` 2 hours ago but is now `Running` and `lookback_minutes=30`
- **THEN** the pod is excluded from results

### Requirement: Failure Reason Grouping

The tool SHALL group results by failure reason category (scheduling, runtime, registry, config) in addition to providing per-pod detail. The grouped summary SHALL include the count of pods per reason.

#### Scenario: Pods grouped by failure category

- **WHEN** the response contains 7 Unschedulable pods and 3 OOMKilled pods
- **THEN** the grouped summary shows `Scheduling: 7 pods` and `Runtime: 3 pods` with per-pod details listed under each group

### Requirement: OOMKill Detection

The tool SHALL detect and flag `OOMKilled` pods specifically, sourcing the container name and memory limit from `containerStatuses[].lastState.terminated` fields.

#### Scenario: OOMKilled pod with container detail

- **WHEN** a pod has a container terminated with reason `OOMKilled`
- **THEN** the response includes the container name, the memory limit that was exceeded, and the restart count

### Requirement: Result Cap

The tool SHALL cap results at 50 pods per response. When results are truncated, the response SHALL include the total matching count and a note indicating truncation.

#### Scenario: Results truncated at 50

- **WHEN** 120 pods match the query filters
- **THEN** the response includes the first 50 pods, a note "Showing 50 of 120 matching pods", and the grouped summary reflects all 120 pods

### Requirement: Event Context Per Pod

The tool SHALL surface the most recent Kubernetes event message per pod to provide root cause context.

#### Scenario: Last event message included

- **WHEN** a pod has associated Kubernetes events
- **THEN** the response includes the most recent event message for that pod (e.g., "0/12 nodes available: Insufficient cpu")

