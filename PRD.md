# Platform MCP Server — Product Requirements Document

| Field | Value |
|---|---|
| Version | 0.3 – Draft |
| Status | In Review |
| Author | Platform Engineering |
| Date | February 2026 |
| Classification | Internal |

---

## 1. Executive Summary

The Platform MCP Server is an internal tool that exposes the GitOps platform's operational data to AI assistants (Claude, Cursor, etc.) through the Model Context Protocol (MCP). It provides platform engineers with natural language access to monitoring, diagnostic, and upgrade-tracking capabilities across the AKS multi-tenant platform — six clusters spanning three environments (dev, staging, prod) and two Azure regions (eastus, westus2) — without requiring context-switches between ArgoCD, kubectl, and the Azure portal. All data is sourced directly from the Kubernetes API and Azure AKS REST API using the official Python client libraries.

> **Scope:** Initial scope covers four use cases: (1) AKS node pool memory and CPU pressure monitoring, (2) Kubernetes version upgrade tracking with in-flight upgrade context and per-node status, (3) upgrade duration metrics derived from Kubernetes events, and (4) failed and pending pod diagnostics. All tools are read-only. No writes to Git, cluster, or pipeline systems are in scope for v1.

---

## 2. Problem Statement

### 2.1 Current State

Platform engineers managing 100+ tenants across six AKS clusters (dev, staging, and production in both eastus and westus2 regions) currently rely on:

- ArgoCD / Akuity SaaS UI for application sync and health state
- kubectl and az CLI for node-level diagnostics, resource utilization queries, and version inspection
- Azure Portal for AKS upgrade path and node pool status
- Azure DevOps pipelines for upgrade orchestration tracking
- Manual `kubectl describe pod` and `kubectl get events` runs to diagnose failing workloads

Answering a question like "which node pools are under pressure right now and are any at autoscaler ceiling?" requires running multiple kubectl commands and cross-referencing node and pod state manually. During an incident, this context-switching adds minutes of triage overhead per engineer per event.

### 2.2 Opportunity

MCP provides a standardized protocol for exposing platform APIs to AI assistants. By wrapping Kubernetes API and Azure AKS REST API calls in MCP tools backed by the official Python client libraries, the platform team can:

- Answer cross-system operational questions in a single conversational turn
- Reduce average incident triage time by eliminating tool-switching
- Surface upgrade risk earlier through proactive, queryable upgrade state
- Establish a composable foundation for future platform AI tooling

---

## 3. Goals & Non-Goals

### 3.1 Goals

- Expose node pool CPU and memory pressure as a queryable MCP tool sourced directly from the Kubernetes Metrics API and node resource fields
- Expose Kubernetes version upgrade status across all AKS clusters and node pools, including in-flight upgrade context and per-node upgrade state
- Expose upgrade duration metrics (elapsed and estimated remaining) derived from Kubernetes events
- Expose failed and pending pod diagnostics with root cause context (scheduling failures, image pull errors, OOM kills, etc.)
- Support Claude Desktop and Claude Code as primary AI client interfaces
- Run as a self-hosted stdio server — credentials never leave the engineer's workstation or the internal network
- Maintain read-only scope for v1 — no mutations to cluster, Git, or pipeline state
- Be extensible: tool structure should make it easy to add future tools (tenant sync state, Kong route health, etc.)

### 3.2 Non-Goals

- Write operations (triggering upgrades, force-syncing ArgoCD apps, modifying cluster resources)
- Tenant-facing exposure — this is a platform team internal tool only
- A web UI or REST API surface — MCP stdio is the only transport for v1
- Multi-user / shared server deployment — each engineer runs their own local process
- Replacing the ArgoCD UI or Azure Portal as primary operational interfaces

---

## 4. Users, Personas & Usage Scenarios

### 4.1 Persona Overview

| Persona | Role | Technical Depth | Primary Concern | Frequency of Use |
|---|---|---|---|---|
| Alex — Platform Engineer | Day-to-day platform ops and development | High (kubectl fluent, writes automation) | Cluster health, workload failures, capacity headroom | Multiple times daily |
| Sam — On-Call Engineer | Incident response rotation (all platform engineers take shifts) | High, but under pressure | Fast root cause, blast radius, incident timeline | Reactive; high urgency when active |
| Jordan — Platform Tech Lead | Architecture decisions, upgrade planning, capacity governance | Very high; sets team standards | Version risk, upgrade sequencing, systemic patterns | Weekly planning + ad hoc |
| Casey — Tenant Engineering Lead | Leads a tenant team; not a platform team member | Medium (Kubernetes aware, not an operator) | Why my workloads are failing, when will it be fixed | Escalation-driven; occasional |

---

### 4.2 Alex — Platform Engineer

**Background.** Alex is a mid-senior platform engineer responsible for day-to-day cluster health across all six clusters (dev, staging, prod × eastus, westus2). Alex writes automation in Python and Typer, is fluent with kubectl, and typically has three terminal windows and the ArgoCD UI open at once. The biggest time sink is correlating information across multiple sources when a tenant reports a problem.

**Goals.** Quickly confirm or rule out platform-level causes for tenant issues. Proactively catch capacity problems before they cause incidents. Keep repetitive diagnostic commands out of the workflow.

**Frustrations.** Having to context-switch between kubectl, the Azure portal, and ArgoCD to answer questions that should have simple answers. Writing the same kubectl one-liners repeatedly. Spending time explaining cluster state to tenant teams in terms they understand.

**Usage scenarios.**

- *Morning health check:* At the start of a shift, Alex asks "give me a summary of cluster health across all environments" and expects node pool pressure, any pending or failing pods, and any active upgrades — all in one response, without running four separate commands.
- *Tenant escalation:* A tenant reports that their batch job pods aren't starting. Alex asks "why are pods pending in the data-pipeline namespace on prod?" and gets a grouped failure breakdown with the specific scheduler rejection message, immediately narrowing it to a resource request issue vs. a node pool capacity issue.
- *Proactive capacity check:* Before a planned load test, Alex asks "which node pools in staging are within 20% of autoscaler max?" to confirm there's headroom, then shares the output with the tenant team.
- *Post-incident review:* Alex asks "show me OOMKilled pods in prod in the last 2 hours" to reconstruct the timeline of a memory-related incident for the post-mortem.

---

### 4.3 Sam — On-Call Engineer

**Background.** Sam is an experienced platform engineer currently on the on-call rotation. During a quiet shift, Sam is focused on other work; during an active incident, Sam needs information fast and accurately. Under pressure, the cognitive cost of constructing the right kubectl command increases — mistakes happen, and wrong commands waste critical minutes.

**Goals.** Determine within 2 minutes whether a reported problem is platform-caused or tenant-caused. Get enough context to start remediation or escalation without switching tools. Communicate cluster state clearly to stakeholders during an incident.

**Frustrations.** Alerts that lack context — knowing something is wrong but not why. Having to remember the exact kubectl syntax for event filtering while also managing a Slack incident channel. Uncertainty about whether an active upgrade is related to a reported problem.

**Usage scenarios.**

- *Incident triage:* PagerDuty fires at 2am. Sam asks "is there anything unusual across the prod cluster right now?" and gets a snapshot: node pool pressure, any failing pods, and whether an upgrade is in progress — before deciding whether to escalate or investigate.
- *Upgrade-related incident:* Tenants report latency spikes. Sam asks "is there an upgrade running on prod right now?" and gets the current node upgrade progress, which nodes are cordoned, and how long the current node has been upgrading — immediately confirming whether the latency correlates with node drain activity.
- *Blast radius assessment:* A namespace is reported as degraded. Sam asks "show me all failing pods in the payments namespace" and gets a list with failure reasons and restart counts, allowing a quick assessment of how many workloads are affected and whether this is spreading.
- *Stakeholder update:* During an incident bridge, Sam asks "how long until the current prod upgrade finishes?" and pastes the estimated completion time into the stakeholder channel, grounding the update in actual node event data rather than a guess.

---

### 4.4 Jordan — Platform Tech Lead

**Background.** Jordan sets the technical direction for the platform team, owns upgrade planning, and is responsible for ensuring the platform stays within Microsoft's Kubernetes support window across all clusters. Jordan is deeply technical but spends more time in planning and review than in terminals. Most interactions with the platform MCP server happen during weekly planning, before upgrade windows, or when reviewing systemic patterns after a run of incidents.

**Goals.** Maintain visibility over version drift across the fleet without having to query each cluster individually. Make confident upgrade scheduling decisions backed by real duration data. Identify systemic patterns (repeated OOMKills, persistent scheduling failures) that point to a configuration or capacity gap.

**Frustrations.** Preparing upgrade plans requires manually checking each cluster's version, available upgrades, and node pool state — a process that takes 20+ minutes and is error-prone. Post-incident reviews often reveal patterns that were visible in the data but not surfaced because nobody was looking at the fleet holistically.

**Usage scenarios.**

- *Weekly version review:* Jordan asks "give me a version summary across all clusters and node pools" and gets a consolidated table showing current versions, support status, and available upgrades — the starting point for the weekly upgrade planning meeting.
- *Upgrade scheduling:* Before scheduling a prod upgrade, Jordan asks "how long have the last three prod upgrades taken for user-pool-general?" and uses the historical P90 duration to size the maintenance window correctly.
- *Systemic pattern review:* After a month with multiple OOMKill incidents, Jordan asks "what is the pod failure distribution by reason across production over the last 7 days?" to quantify the scope and make the case for a memory limit policy review.
- *Deprecation urgency check:* Jordan asks "which clusters or node pools are within 60 days of Kubernetes end-of-support?" to get a prioritized list for upgrade scheduling, ensuring nothing slips through.
- *Upgrade risk assessment:* Before approving a production upgrade, Jordan asks "is the current upgrade pace on staging on par with historical baselines?" to confirm the upgrade is proceeding normally before promoting the same version to prod.

---

### 4.5 Casey — Tenant Engineering Lead

**Background.** Casey leads an engineering team that runs workloads on the platform but is not a platform team member. Casey understands Kubernetes at a workload level (deployments, resource requests, pod lifecycle) but doesn't have kubectl access to the platform clusters and doesn't know the node pool topology. When workloads fail, Casey relies on the platform team to provide context — but wants enough information to understand the platform's role vs. their own team's responsibility.

**Goals.** Understand quickly whether a workload failure is caused by the platform (capacity, upgrades, node issues) or by the tenant's own configuration or code. Get a clear timeline when platform-caused issues are affecting workloads. Self-serve on basic diagnostics without waiting for a platform engineer response.

**Frustrations.** Waiting 15–30 minutes for a platform engineer to confirm "yes, we're upgrading, that's why your pods were evicted." Not knowing whether to roll back a deployment or wait for a platform issue to resolve. Opaque error messages that don't distinguish platform causes from application causes.

> **Note:** Casey does not directly interact with the MCP server — this is a platform team internal tool. However, the MCP server enables platform engineers to generate clear, structured status updates for Casey's team quickly and accurately. Casey's needs shape the output format and language of tool responses.

**Scenarios where Casey's needs shape tool output.**

- *Upgrade impact communication:* When Casey's team reports pod evictions, a platform engineer uses `get_upgrade_progress` and pastes a plain-English summary into the shared incident channel: "The prod cluster is currently upgrading node pool user-pool-general. 8 of 12 nodes are done, estimated completion in ~23 minutes. Pod evictions during this window are expected."
- *Failure attribution:* When Casey escalates "our payment-processor pods keep restarting," a platform engineer runs `get_pod_health` and immediately identifies OOMKills at the 512Mi limit — then shares the finding with Casey as a clear tenant-side configuration issue, not a platform failure.
- *Capacity context:* When Casey's team wants to scale a batch workload, a platform engineer checks `check_node_pool_pressure` and shares whether the target node pool has capacity before Casey's team sets resource requests.

---

## 5. Use Cases

> **Cluster identifier convention.** All tools use a composite `cluster` parameter that encodes both the environment and region. The valid values are: `dev-eastus`, `dev-westus2`, `staging-eastus`, `staging-westus2`, `prod-eastus`, `prod-westus2`, and `all`. The mapping from cluster identifier to AKS resource group, subscription, and kubeconfig context is defined in `config.py`.

### UC-01 · Node Pool Pressure Monitoring

#### 5.1.1 Context

Platform engineers need to quickly assess whether AKS node pools are approaching resource saturation — either from high request-to-allocatable ratios or from autoscaler having reached max node count, resulting in pending pods.

#### 5.1.2 User Stories

- **US-01:** As a platform engineer, I want to ask "what is the current CPU and memory pressure across all node pools in production?" and receive a per-pool breakdown with pressure levels, so I can identify saturation risk without opening Datadog.
- **US-02:** As an on-call engineer, I want to ask "are there any pending pods due to scheduling failures?" and see which node pools are affected, so I can correlate with autoscaler events during incident triage.
- **US-03:** As a platform engineer, I want to compare pressure across clusters and regions (e.g., staging-eastus vs. staging-westus2), so I can validate that a load pattern in one region hasn't produced unexpected capacity pressure elsewhere.
- **US-04:** As a platform tech lead, I want to ask "which node pools are within 10% of autoscaler max?", so I can proactively request node pool quota increases before hitting ceilings.

#### 5.1.3 Tool Specification

| Field | Value |
|---|---|
| Tool name | `check_node_pool_pressure` |
| Data source | Kubernetes Metrics API (`metrics.k8s.io/v1beta1`) for CPU/memory usage; Kubernetes Core API (`v1`) for node allocatable capacity, node labels (`agentpool`), and pod scheduling state |
| Parameters | `cluster: enum [dev-eastus, dev-westus2, staging-eastus, staging-westus2, prod-eastus, prod-westus2, all]` |
| Returns | Per-pool: CPU requests % of allocatable, memory requests % of allocatable, pending pod count, ready node count, max node count from cluster-autoscaler annotation, pressure level (`ok` / `warning` / `critical`) |
| Latency target | < 3 seconds P95 |
| Auth | Kubernetes API via kubeconfig resolved from cluster config mapping (`az aks get-credentials` session) |

#### 5.1.4 Pressure Level Thresholds

| Level | CPU Requests / Allocatable | Memory Requests / Allocatable | Pending Pods |
|---|---|---|---|
| 🔴 Critical | ≥ 90% | ≥ 95% | > 10 |
| 🟡 Warning | ≥ 75% | ≥ 80% | > 0 |
| ✅ OK | < 75% | < 80% | 0 |

> **Pressure level resolution:** A node pool's overall pressure level is the **highest severity** across all three metrics (CPU, memory, pending pods). For example, a pool at 91% CPU (critical), 82% memory (warning), and 0 pending pods (ok) is reported as `critical`.
>
> Threshold values are externalized to `config.py` and should be agreed with the team. The defaults above are starting points based on typical AKS workload patterns.

---

### UC-03 · Failed & Pending Pod Diagnostics

#### 5.3.1 Context

Pending and failed pods are often the first visible symptom of a deeper platform problem — node pressure, image registry issues, misconfigured resource requests, OOM kills, or scheduling constraint mismatches. Today, diagnosing the cause requires manually running `kubectl describe pod`, `kubectl get events`, and cross-referencing node state. This tool surfaces root cause context in a single query.

#### 5.3.2 User Stories

- **US-05:** As an on-call engineer, I want to ask "why are pods pending in the prod cluster?" and receive a grouped breakdown by failure reason, so I can distinguish between a scheduling constraint issue and a node capacity issue without running kubectl.
- **US-06:** As a platform engineer, I want to ask "show me all failed pods in the frontend namespace" and see the failure reason, last restart count, and relevant events, so I can quickly assess whether this is a transient crash or a systemic problem.
- **US-07:** As an on-call engineer, I want to ask "are there any OOMKilled pods in the last 30 minutes?" and see which workloads and namespaces are affected, so I can correlate with a memory pressure spike.
- **US-08:** As a platform engineer, I want to ask "are there any image pull failures across the cluster?" so I can identify registry connectivity or credential issues before they spread to more pods.
- **US-09:** As a platform tech lead, I want to ask "what is the current pod failure distribution by reason across production?" so I can identify systemic patterns that warrant a configuration or capacity change.

#### 5.3.3 Tool Specification

| Field | Value |
|---|---|
| Tool name | `get_pod_health` |
| Data source | Kubernetes Core API (`v1`): `pods` resource for status, phase, container state, restart counts; `events` resource filtered by `involvedObject.kind=Pod` for root cause messages |
| Parameters | `cluster: enum [dev-eastus, dev-westus2, staging-eastus, staging-westus2, prod-eastus, prod-westus2, all]`, `namespace: str (optional, default: all)`, `status_filter: enum [pending, failed, all] (default: all)`, `lookback_minutes: int (default: 30)` |
| Returns | Per-pod: name, namespace, node, status, reason, restart count, last event message, age; grouped summary by failure reason |
| Latency target | < 4 seconds P95 |
| Auth | Kubernetes API via kubeconfig resolved from cluster config mapping |

> **Lookback semantics:** `lookback_minutes` filters *resolved or transient* failures by event time. Pods that are **currently in an unhealthy state** (Pending, CrashLoopBackOff, ImagePullBackOff, etc.) are always included regardless of age — a pod that has been Pending for 3 hours is still an active problem and must appear in results even with `lookback_minutes=30`.

#### 5.3.4 Pod Failure Reason Taxonomy

| Reason | Category | Typical Cause |
|---|---|---|
| `Pending / Unschedulable` | Scheduling | Insufficient CPU/memory, node selector mismatch, taint/toleration missing |
| `Pending / PodFitsResources` | Scheduling | Node pool at capacity; autoscaler may be scaling |
| `OOMKilled` | Runtime | Container exceeded memory limit; may indicate limit is too low |
| `CrashLoopBackOff` | Runtime | Application error on startup; check logs |
| `ImagePullBackOff` | Registry | Image not found or registry credentials invalid |
| `ErrImagePull` | Registry | Transient registry connectivity failure |
| `CreateContainerConfigError` | Config | Missing ConfigMap or Secret reference |
| `Error` | Runtime | Generic container exit with non-zero code |

---

### UC-02 · Kubernetes Version Upgrade Monitoring

#### 5.2.1 Context

Engineers need visibility into current Kubernetes versions, available upgrades, upgrade eligibility, and the status of in-flight operations without navigating the Azure portal. When an upgrade is active, engineers additionally need per-node upgrade state, elapsed duration, an estimated completion time derived from Kubernetes node events, and any PodDisruptionBudget violations that may be blocking cordon and drain — so they can distinguish a healthy upgrade in progress from one that has stalled due to a workload constraint.

#### 5.2.2 User Stories

- **US-10:** As a platform tech lead, I want to ask "what Kubernetes versions are running across all clusters and node pools?" and see a consolidated view, so I can identify version drift between environments.
- **US-11:** As a platform engineer, I want to ask "what upgrades are available for the production cluster?" and see the available versions with Microsoft's support status, so I can plan upgrade windows.
- **US-12:** As an on-call engineer, I want to ask "is there an active upgrade running on any cluster right now?" and get current upgrade state, which node pool is upgrading, and how long it has been running, so I can determine whether an incident is upgrade-related.
- **US-13:** As a platform tech lead, I want to ask "which node pools are on deprecated Kubernetes versions?" so I can prioritize upgrade sequencing before end-of-support dates.
- **US-14:** As a platform engineer, I want to ask "what is the minimum supported version for AKS right now?" so I can assess our upgrade urgency relative to Microsoft's support window.
- **US-15:** As an on-call engineer, I want to ask "show me the node-by-node upgrade status for the prod system pool" and see which nodes are upgraded, upgrading, cordoned, or pending, so I can assess upgrade progress and spot stalled nodes.
- **US-16:** As a platform engineer, I want to ask "how long has the current upgrade been running and when is it expected to finish?" and receive an elapsed time and estimated remaining duration, so I can set expectations with tenants. Given our upgrades normally complete within 60 minutes, any estimate significantly beyond that should be flagged.
- **US-17:** As a platform tech lead, I want to ask "is the current upgrade taking longer than previous upgrades for this node pool?" so I can determine whether to investigate or escalate to Microsoft support.
- **US-18:** As a platform engineer, I want to ask "are any PodDisruptionBudgets blocking the current upgrade?" before or during an upgrade, so I can identify workloads preventing cordon and drain before they stall a node and delay the pipeline.

#### 5.2.3 Tool Specification — Version & Upgrade State

| Field | Value |
|---|---|
| Tool name | `get_kubernetes_upgrade_status` |
| Data source | Azure AKS REST API (`ManagedClusters`, `AgentPools`, `upgradeProfiles`) |
| Parameters | `cluster: enum [dev-eastus, dev-westus2, staging-eastus, staging-westus2, prod-eastus, prod-westus2, all]` |
| Returns | Control plane version, available upgrades, per-node-pool version and upgrade eligibility, active upgrade state, support status per version |
| Latency target | < 5 seconds P95 (AKS API is not cached) |
| Auth | Azure `DefaultCredential` (picks up existing `az login` session via env) |

#### 5.2.4 Tool Specification — In-Flight Upgrade Context

| Field | Value |
|---|---|
| Tool name | `get_upgrade_progress` |
| Data source | Azure AKS REST API (`AgentPools`) for pool-level state; Kubernetes Events API (`reason: NodeUpgrade`, `reason: NodeReady`) for per-node timing |
| Parameters | `cluster: enum [dev-eastus, dev-westus2, staging-eastus, staging-westus2, prod-eastus, prod-westus2, all]`, `node_pool: str (optional, default: all upgrading pools)` |
| Returns | Per-node: name, current state (`upgraded`, `upgrading`, `cordoned`, `pdb_blocked`, `pending`, `stalled`), node version, time in current state; pool-level: nodes total, nodes upgraded, nodes remaining, elapsed duration, estimated remaining duration, upgrade start timestamp |
| Latency target | < 5 seconds P95 |
| Auth | Azure `DefaultCredential` + Kubernetes API session |

#### 5.2.5 Tool Specification — Upgrade Duration Metrics

| Field | Value |
|---|---|
| Tool name | `get_upgrade_duration_metrics` |
| Data source | Kubernetes Events API (`NodeUpgrade`, `NodeReady`, `NodeNotReady` events) for the current in-progress upgrade; AKS Activity Log for completed historical upgrade records (retained for 90 days — well beyond the 1-hour Kubernetes event TTL) |
| Parameters | `cluster: enum [dev-eastus, dev-westus2, staging-eastus, staging-westus2, prod-eastus, prod-westus2, all]`, `node_pool: str`, `history_count: int (default: 5)` |
| Returns | Current upgrade: elapsed time, estimated remaining (based on mean time-per-node from current run); Historical: last N upgrade durations per pool, mean duration, P90 duration, slowest and fastest node in current run; over-time flag if elapsed exceeds 60-minute expected baseline |
| Latency target | < 6 seconds P95 |
| Auth | Azure `DefaultCredential` + Kubernetes API session |

> **Duration estimation methodology:** Estimated remaining time is calculated as `mean_seconds_per_node_so_far × nodes_remaining`. The mean is derived from the delta between `NodeUpgrade` (cordoned) and `NodeReady` events for each completed node in the current run — sourced from the Kubernetes Events API, which has sufficient TTL for a single upgrade run. Historical durations for past runs are sourced from the AKS Activity Log, which retains records for 90 days and is not subject to the 1-hour event TTL. Because our ADO pipeline + Terraform upgrade process is designed to complete within 60 minutes under normal conditions, the tool flags any estimated completion time beyond 60 minutes from upgrade start as potentially anomalous.

#### 5.2.6 Tool Specification — PodDisruptionBudget Preflight & Drain Blocker Check

| Field | Value |
|---|---|
| Tool name | `check_pdb_upgrade_risk` |
| Data source | Kubernetes Policy API (`policy/v1`): `poddisruptionbudgets` across all namespaces; Kubernetes Core API (`v1`): current pod counts per workload to evaluate PDB satisfiability |
| Parameters | `cluster: enum [dev-eastus, dev-westus2, staging-eastus, staging-westus2, prod-eastus, prod-westus2, all]`, `node_pool: str (optional)`, `mode: enum [preflight, live] (default: preflight)` |
| Returns | **Preflight mode:** List of PDBs where `maxUnavailable=0` or `minAvailable` equals current ready replica count — i.e., PDBs that would block drain if any pod were evicted; affected workload name, namespace, current ready/desired counts, PDB rule. **Live mode (during active upgrade):** PDBs currently blocking eviction on cordoned nodes, with the specific pods and nodes affected and time blocked. |
| Latency target | < 4 seconds P95 |
| Auth | Kubernetes API via kubeconfig |

> **When to use each mode.** Run `preflight` before triggering an upgrade via the ADO pipeline to surface workloads that will block drain. Run `live` during an in-progress upgrade when a node appears stalled in the `cordoned` state beyond expected drain time — this identifies the specific PDB and workload preventing eviction so the team can decide whether to intervene or wait.

#### 5.2.7 Upgrade State Model

| State | Meaning | How Surfaced |
|---|---|---|
| 🔵 Upgrading (node) | This specific node is currently being upgraded | `NodeUpgrade` event present; `NodeReady` not yet seen |
| 🟠 Cordoned | Node is cordoned and draining; upgrade imminent | Node `spec.unschedulable = true`; no `NodeUpgrade` event yet |
| 🚫 PDB Blocked | Node is cordoned but drain is blocked by a PodDisruptionBudget | Cordoned node + active eviction attempt in events + PDB `disruptionsAllowed = 0` |
| ⏳ Pending | Node is queued for upgrade; not yet cordoned | Pool is upgrading; node shows old version; not yet cordoned |
| ✅ Upgraded | Node is on target version and ready | `NodeReady` event after `NodeUpgrade`; version matches target |
| 🔴 Stalled | Node has been cordoned or upgrading beyond the 60-minute pool-level expected window, with no PDB explanation | Elapsed upgrade time > 60 min and node not yet `NodeReady`; no blocking PDB detected |
| 🟡 Upgrade Available | Pool upgrade available but not started | `availableUpgrades` list non-empty |
| ⛔ Deprecated | Version at or past end-of-support date | Derived from AKS support calendar |

---

## 6. Functional Requirements

| ID | Requirement | Use Case | Priority |
|---|---|---|---|
| FR-01 | Server exposes `check_node_pool_pressure` tool via MCP stdio transport | UC-01 | Must Have |
| FR-02 | `check_node_pool_pressure` queries the Kubernetes Metrics API and Core API to derive CPU/memory request ratios and pending pod counts, grouped by node pool via the `agentpool` node label | UC-01 | Must Have |
| FR-03 | Pressure level (`ok`/`warning`/`critical`) is derived from configurable thresholds defined in `config.py` | UC-01 | Must Have |
| FR-04 | Tool response includes human-readable summary line suitable for LLM context | UC-01 | Must Have |
| FR-05 | Server exposes `get_kubernetes_upgrade_status` tool via MCP stdio transport | UC-02 | Must Have |
| FR-06 | `get_kubernetes_upgrade_status` queries AKS API for control plane version, node pool versions, and upgrade availability | UC-02 | Must Have |
| FR-07 | Tool surfaces active upgrade state (in-progress vs. idle) per cluster and node pool | UC-02 | Must Have |
| FR-08 | Tool flags node pools on deprecated / near-end-of-support versions | UC-02 | Should Have |
| FR-09 | All tools support `cluster=all` parameter to query all six clusters in parallel | UC-01, UC-02, UC-03 | Should Have |
| FR-10 | Tool docstrings accurately describe when and how to invoke each tool (used by LLM for tool selection) | All | Must Have |
| FR-11 | Credentials are sourced exclusively from environment variables and kubeconfig; no hardcoded secrets | All | Must Have |
| FR-12 | All tool errors return structured error messages (not raw stack traces) suitable for LLM consumption | All | Must Have |
| FR-13 | Response latency for `check_node_pool_pressure` is < 3s P95 | UC-01 | Should Have |
| FR-14 | Response latency for `get_kubernetes_upgrade_status` is < 5s P95 | UC-02 | Should Have |
| FR-15 | Server exposes `get_upgrade_progress` tool that returns per-node upgrade state for in-flight upgrades | UC-02 | Must Have |
| FR-16 | `get_upgrade_progress` queries Kubernetes Events API to derive per-node state (`upgraded`, `upgrading`, `cordoned`, `pdb_blocked`, `pending`, `stalled`) | UC-02 | Must Have |
| FR-17 | `get_upgrade_progress` returns node pool totals: nodes upgraded, upgrading, remaining, elapsed time, and estimated time remaining | UC-02 | Must Have |
| FR-18 | A node is flagged as `stalled` when the total pool upgrade has exceeded 60 minutes and the node is not yet `NodeReady`, with no active PDB block detected | UC-02 | Must Have |
| FR-19 | Server exposes `get_upgrade_duration_metrics` tool that returns elapsed, estimated remaining, and historical upgrade durations per node pool | UC-02 | Must Have |
| FR-20 | Duration estimation uses mean seconds-per-node from the current upgrade run applied to remaining node count | UC-02 | Must Have |
| FR-21 | Historical duration data covers last 5 upgrades by default (configurable); sourced from AKS Activity Log (90-day retention); includes mean, P90, min, and max per-pool | UC-02 | Should Have |
| FR-22a | Current run per-node timing is derived from Kubernetes `NodeUpgrade`/`NodeReady` event deltas via the Events API, which has sufficient TTL for a single upgrade run | UC-02 | Must Have |
| FR-22b | Historical upgrade duration records are sourced from the AKS Activity Log (90-day retention) to avoid the 1-hour Kubernetes event TTL constraint | UC-02 | Must Have |
| FR-23 | Server exposes `get_pod_health` tool that returns pending and failed pods with failure reason, restart count, and last event message | UC-03 | Must Have |
| FR-24 | `get_pod_health` accepts `namespace`, `status_filter`, and `lookback_minutes` parameters | UC-03 | Must Have |
| FR-25 | Tool groups results by failure reason category (scheduling, runtime, registry, config) in addition to per-pod detail | UC-03 | Must Have |
| FR-26 | Tool surfaces the most recent Kubernetes event message per pod to provide root cause context without requiring kubectl | UC-03 | Must Have |
| FR-27 | Tool detects and flags `OOMKilled` pods specifically, sourcing container name and memory limit from the pod's `containerStatuses[].lastState.terminated` fields | UC-03 | Should Have |
| FR-28 | Response latency for `get_pod_health` is < 4s P95 | UC-03 | Should Have |
| FR-29 | Server exposes `check_pdb_upgrade_risk` tool with `preflight` and `live` modes | UC-02 | Must Have |
| FR-30 | In `preflight` mode, `check_pdb_upgrade_risk` evaluates all PDBs across all namespaces and flags any where `maxUnavailable=0` or `minAvailable` equals the current ready pod count — indicating drain would be blocked if any pod were evicted | UC-02 | Must Have |
| FR-31 | In `live` mode, `check_pdb_upgrade_risk` identifies PDBs currently blocking eviction on cordoned nodes, reporting the PDB name, namespace, affected pods, and time the block has been active | UC-02 | Must Have |
| FR-32 | When a node is in `pdb_blocked` state in `get_upgrade_progress`, the output includes a direct reference to the blocking PDB and a suggestion to run `check_pdb_upgrade_risk(mode="live")` for full detail | UC-02 | Must Have |
| FR-33 | `get_upgrade_duration_metrics` flags any estimated or elapsed total upgrade duration exceeding 60 minutes as potentially anomalous, with a note that ADO pipeline upgrades are expected to complete within that window | UC-02 | Should Have |
| FR-34 | `get_pod_health` caps results at 50 pods per response; when truncated, the response includes the total matching count and a note that results were capped | UC-03 | Should Have |
| FR-35 | In `preflight` mode, when `node_pool` is provided, `check_pdb_upgrade_risk` filters to PDBs governing pods with replicas currently scheduled on the specified node pool; when `node_pool` is omitted, all PDBs cluster-wide are evaluated | UC-02 | Must Have |

---

## 7. Non-Functional Requirements

### 7.1 Security

- All credentials (Azure auth tokens, kubeconfig) must be provided via environment variables and kubeconfig files; never logged or surfaced in tool output
- Server runs via stdio transport only — no network listener is opened; there is no attack surface for remote clients
- Azure authentication uses `DefaultAzureCredential`, inheriting the engineer's existing `az login` session — no service principal credentials are stored locally
- Kubernetes API access uses kubeconfig resolved from the cluster config mapping; the kubeconfig context is explicitly set per-call to prevent cross-cluster contamination
- All tools are read-only; the server must not expose any write operations against AKS, ArgoCD, Azure DevOps, or Git
- Tool output must be scrubbed of internal IP addresses, Azure subscription IDs, and resource group names before being returned to the LLM context. Node names (e.g., `aks-userpool-000011`) are operational identifiers required for troubleshooting and are safe to surface

### 7.2 Reliability

- If the Kubernetes Metrics API is unavailable (e.g., metrics-server not running), `check_node_pool_pressure` must return a structured error indicating the missing data source, and still return any data available from the Core API (node allocatable, pending pods)
- If AKS API returns partial data (e.g., one cluster unavailable), `get_kubernetes_upgrade_status` must return available data with a clear indication of what is missing
- The MCP server process must restart automatically if it crashes — engineers should configure this via their AI client's MCP server config

### 7.3 Maintainability

- Thresholds (CPU %, memory %, pending pod limits) must be externalized to a config file or environment variables — not hardcoded
- Cluster name-to-AKS-resource-group mapping must be externalized to config
- Each tool must have its own module under `tools/` to enable independent testing and future extension
- Pydantic models must be used for all tool inputs and outputs to enable schema validation and documentation generation

### 7.4 Error Model

All tools must return errors using a consistent Pydantic model. Raw stack traces must never reach the LLM context.

```python
class ToolError(BaseModel):
    error: str              # Human-readable error summary suitable for LLM consumption
    source: str             # Data source that failed (e.g., "metrics-server", "aks-api")
    cluster: str            # Cluster context where the error occurred
    partial_data: bool      # True if the response includes partial results alongside this error
```

When `partial_data` is `True`, the tool response includes both the error and whatever data was successfully retrieved from other sources. This enables graceful degradation — for example, returning Core API data alongside a metrics-server unavailability error.

### 7.5 Observability

- Structured logging (JSON) to stderr — MCP clients forward stderr to their log facilities
- Each tool invocation logs: tool name, parameters, data source latency, and success/failure
- No PII or credential values in logs

---

## 8. Scope Boundary

| Capability | v1 Status | Rationale |
|---|---|---|
| Node pool CPU/memory pressure query via Kubernetes Metrics API | ✅ In Scope | Core UC-01 |
| Node pool pending pod count via Kubernetes Core API | ✅ In Scope | Required for scheduling pressure signal |
| Failed & pending pod diagnostics with root cause | ✅ In Scope | Core UC-03 |
| OOMKilled pod detection with container and limit detail | ✅ In Scope | Core UC-03 |
| Pod failure grouping by reason category | ✅ In Scope | Core UC-03 |
| Kubernetes version query across clusters | ✅ In Scope | Core UC-02 |
| Available upgrade paths per cluster | ✅ In Scope | Core UC-02 |
| Active upgrade state detection | ✅ In Scope | Core UC-02 |
| Per-node upgrade state during in-flight upgrades | ✅ In Scope | Core UC-02 |
| PDB preflight check before upgrade | ✅ In Scope | Core UC-02 |
| PDB live drain-blocker detection during active upgrade | ✅ In Scope | Core UC-02 |
| Upgrade elapsed time and estimated remaining duration | ✅ In Scope | Core UC-02 |
| Historical upgrade duration metrics via AKS Activity Log (90-day retention) | ✅ In Scope | Core UC-02; solves Kubernetes event TTL constraint |
| 60-minute anomaly flag on upgrade duration | ✅ In Scope | Reflects ADO pipeline expected completion baseline |
| Stalled node detection (no PDB block, >60 min elapsed) | ✅ In Scope | Core UC-02 |
| Triggering AKS upgrades | ❌ Out of Scope | Write operation — v2+ |
| ArgoCD application sync state | ❌ Out of Scope | Separate tool — v2+ |
| Tenant-level resource utilization | ❌ Out of Scope | Requires namespace-to-tenant mapping — v2+ |
| Kong route health | ❌ Out of Scope | v2+ |
| External monitoring system integrations (e.g. Datadog, Prometheus) | ❌ Out of Scope | All metrics sourced from Kubernetes API in v1 |
| Remote / shared MCP server deployment | ❌ Out of Scope | Auth maturity needed — v2+ |

---

## 9. Technical Architecture

### 9.1 Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| MCP Framework | `mcp[cli]` (Python FastMCP) | Official Anthropic SDK; simplest tool definition pattern |
| Language | Python 3.11+ | Matches existing platform tooling (Typer CLIs, Pydantic) |
| Package Management | `uv` | Fast, lockfile-based; consistent with platform Python tooling |
| Kubernetes Client | `kubernetes` (official Python client) | Core API (pods, nodes, events), Metrics API (`metrics.k8s.io`), and Events API; uses kubeconfig from `az aks get-credentials` |
| Azure Client | `azure-mgmt-containerservice` + `azure-identity` | AKS management plane: cluster versions, node pool state, upgrade profiles; `DefaultAzureCredential` for auth |
| Validation | Pydantic v2 | Consistent with existing platform CLIs; schema documentation |
| Transport | stdio (local) | No network exposure; credentials stay local |

### 9.2 Project Layout

```text
platform-mcp-server/
├── src/
│   └── platform_mcp_server/
│       ├── __init__.py
│       ├── server.py               # MCP server entry point; tool registrations
│       ├── config.py               # Threshold config, cluster→region/resource-group mapping, kubeconfig context map
│       ├── models.py               # Pydantic models for all tool inputs/outputs
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── node_pools.py       # check_node_pool_pressure
│       │   ├── pod_health.py       # get_pod_health
│       │   ├── k8s_upgrades.py     # get_kubernetes_upgrade_status
│       │   ├── upgrade_progress.py # get_upgrade_progress (per-node state)
│       │   ├── upgrade_metrics.py  # get_upgrade_duration_metrics
│       │   └── pdb_check.py        # check_pdb_upgrade_risk (preflight + live)
│       └── clients/
│           ├── __init__.py
│           ├── k8s_core.py         # Kubernetes Core API wrapper (nodes, pods, namespaces)
│           ├── k8s_metrics.py      # Kubernetes Metrics API wrapper (CPU/memory usage)
│           ├── k8s_events.py       # Kubernetes Events API wrapper (NodeUpgrade, NodeReady, pod events)
│           ├── k8s_policy.py       # Kubernetes Policy API wrapper (PodDisruptionBudgets)
│           └── azure_aks.py        # AKS REST API wrapper (versions, upgrade profiles, activity log)
├── tests/
│   ├── conftest.py                 # Shared fixtures: mock K8s client, mock Azure client, cluster config
│   ├── fixtures/                   # Static test data (JSON responses, node lists, event payloads)
│   ├── test_node_pools.py
│   ├── test_pod_health.py
│   ├── test_k8s_upgrades.py
│   ├── test_upgrade_progress.py
│   ├── test_upgrade_metrics.py
│   ├── test_pdb_check.py
│   └── test_clients/
│       ├── conftest.py
│       ├── test_k8s_core.py
│       ├── test_k8s_metrics.py
│       ├── test_k8s_events.py
│       ├── test_k8s_policy.py
│       └── test_azure_aks.py
├── .devcontainer/
│   └── devcontainer.json           # Dev container configuration
├── .vscode/
│   ├── settings.json               # Workspace settings (formatter, linter, interpreter)
│   └── extensions.json             # Recommended extensions
├── .pre-commit-config.yaml
├── uv.lock
└── pyproject.toml
```

### 9.3 Claude Desktop Integration

```json
{
  "mcpServers": {
    "platform-mcp": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/platform-mcp", "python", "server.py"],
      "env": {
        "AZURE_SUBSCRIPTION_ID": "...",
        "KUBECONFIG": "/home/engineer/.kube/config"
      }
    }
  }
}
```

> Kubernetes context switching between clusters is handled internally by the server using the cluster config mapping in `config.py`. Engineers only need a single merged kubeconfig with contexts for all six clusters, which is the standard output of running `az aks get-credentials` for each cluster.

---

## 10. Milestones & Phasing

| Milestone | Deliverable | Target | Priority |
|---|---|---|---|
| M1 – Scaffolding | Project layout, FastMCP server running locally, Claude Desktop connected, Kubernetes client configured with multi-cluster context map | Week 1 | P2 – Medium |
| M2 – UC-01 Alpha | `check_node_pool_pressure` returning live data from Kubernetes Metrics API and Core API for a single cluster (e.g., dev-eastus) | Week 2 | P2 – Medium |
| M3 – UC-03 Alpha | `get_pod_health` returning live pod state and events for a single cluster (e.g., dev-eastus), grouped by failure reason | Week 2 | P1 – High |
| M4 – UC-01 Complete | All six clusters; thresholds externalized; error handling for metrics-server unavailability | Week 3 | P1 – High |
| M5 – UC-03 Complete | All clusters; OOMKill detail from `containerStatuses`; namespace and lookback filtering | Week 3 | P1 – High |
| M6 – UC-02 Alpha | `get_kubernetes_upgrade_status` returning live AKS data for a single cluster (e.g., dev-eastus) | Week 3 | P1 – High |
| M7 – UC-02 PDB Check | `check_pdb_upgrade_risk` preflight and live modes operational; integrated with `get_upgrade_progress` output | Week 4 | P1 – High |
| M8 – UC-02 In-Flight | `get_upgrade_progress` returning per-node state with `pdb_blocked` and `stalled` states; 60-min anomaly flag active | Week 4 | P1 – High |
| M9 – UC-02 Duration | `get_upgrade_duration_metrics` with current run timing from Events API and historical baselines from AKS Activity Log | Week 5 | P1 – High |
| M10 – UC-02 Complete | Deprecated version flagging; all clusters; stalled node detection confirmed against 60-min baseline | Week 5 | P1 – High |
| M11 – Team Rollout | All platform engineers configured; internal feedback collected | Week 6 | P0 – Critical |
| M12 – v2 Scoping | Backlog groomed based on usage patterns; next tool set agreed | Week 7 | P2 – Medium |

---

## 11. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Kubernetes Metrics API (`metrics-server`) unavailable in a cluster | Low | Medium | `check_node_pool_pressure` degrades gracefully: returns Core API data (pending pods, allocatable) with a clear note that utilization metrics are unavailable; does not crash |
| Node pool label (`agentpool`) not present on nodes in a cluster | Low | High | Validate label presence during M1 scaffolding; add fallback to `kubernetes.azure.com/agentpool` label; surface warning in output if neither label is found |
| AKS API rate limits triggered by parallel cluster queries | Low | Medium | Implement per-tool request caching with short TTL (30s); add retry with exponential backoff |
| MCP protocol spec changes break tool registration patterns | Low | Medium | Pin `mcp[cli]` version; monitor Anthropic SDK changelog; abstract tool registration |
| Engineers configure write-capable service principals instead of user credentials | Low | High | Document setup guide explicitly requiring `az login` user auth; add startup check that validates credential type |
| LLM misinterprets tool output and gives incorrect operational guidance | Medium | Medium | Tool output includes explicit data timestamps and source attribution; engineers treat AI output as a starting point, not ground truth |
| Kubernetes Events API TTL (1 hour) insufficient for historical upgrade duration data | Low | Low | **Resolved:** Historical data sourced from AKS Activity Log (90-day retention). Events API used only for current in-progress run timing, where TTL is not a concern. |
| PDB drain blocker not detected in `live` mode because eviction attempt has not yet been issued by the kubelet | Medium | Medium | In `live` mode, supplement eviction event detection with direct PDB satisfiability evaluation: if a cordoned node has pods whose PDB would evaluate to `disruptionsAllowed=0`, flag proactively without waiting for an eviction failure event |
| 60-minute anomaly threshold produces false positives for pools with many nodes or complex PDB drain scenarios | Low | Medium | Make the 60-minute threshold configurable in `config.py`; suppress the flag if a PDB block is the known cause; annotate flag with context ("total elapsed" vs "node-level elapsed") |
| `get_pod_health` returns excessive noise in large namespaces with many transient failures | Medium | Low | Default `lookback_minutes=30`; cap results at 50 pods per query with a note when truncated; group by reason to surface patterns over individual pods |
| Kubernetes client kubeconfig points to wrong cluster context when `cluster=all` is used | Low | High | Explicitly resolve cluster context from config mapping before each API call; validate context before returning data; surface context name in all responses |

---

## 12. Open Questions

- **OQ-01:** Should thresholds be team-wide config (committed to the repo) or per-engineer overrides? Recommend committed defaults with env var overrides.
- **OQ-02:** What is the right time window for pressure metrics derived from the Kubernetes Metrics API? The API returns a point-in-time snapshot, not an average — should the tool call multiple times and average, or accept that a single snapshot may be momentary?
- **OQ-03:** Should `get_upgrade_progress` surface node pool cordon/drain status even when no upgrade is active, or only during in-flight upgrades?
- **OQ-05:** Should the server be added to the platform's internal developer portal as a documented tool, or remain informal documentation in the repo README?
- **OQ-07:** Should `get_pod_health` include log tail (last N lines) per failing pod via the Kubernetes logs API, or is the event message sufficient for v1? Log tail significantly increases latency and output size.
- **OQ-08:** For `OOMKilled` pods, should the tool recommend a new memory limit based on the last observed working RSS (available from `containerStatuses[].lastState`), or only report the breach?
- **OQ-09:** Should `check_pdb_upgrade_risk` in `preflight` mode be surfaced automatically when an engineer asks about upgrade readiness, or only when explicitly called? Recommend the former — automatically include PDB preflight output in any response involving upgrade planning.

### Resolved Questions

- **OQ-04 — Resolved:** All tools support `cluster=all` parallel query mode in v1. See FR-09.
- **OQ-06 — Resolved:** Historical upgrade duration data is sourced from the AKS Activity Log (90-day retention), which is not subject to the Kubernetes event TTL constraint. Kubernetes Events API is used only for current in-progress run timing. See FR-22a/FR-22b.

---

## 13. Appendix: Example Interactions by Persona

### A. Alex (Platform Engineer) — Morning Health Check

```
Alex: "Give me a health summary for all clusters before standup."

Claude: [calls check_node_pool_pressure(cluster="all")]
        [calls get_pod_health(cluster="all", lookback_minutes=60)]

Cluster health snapshot — 08:47 UTC

dev-eastus
  system-pool      ✅  CPU: 22%  |  Memory: 48%  |  Nodes: 2
  user-pool        ✅  CPU: 41%  |  Memory: 53%  |  Nodes: 6
  No failing pods.

dev-westus2
  system-pool      ✅  CPU: 20%  |  Memory: 45%  |  Nodes: 2
  user-pool        ✅  CPU: 38%  |  Memory: 50%  |  Nodes: 6
  No failing pods.

staging-eastus
  system-pool      ✅  CPU: 19%  |  Memory: 51%  |  Nodes: 2
  user-pool        🟡  CPU: 76%  |  Memory: 74%  |  Nodes: 8  (1 node from autoscaler max)
  1 failing pod: spark-test-job-001 (data-pipeline) — CrashLoopBackOff,
  "Error: connection refused to postgres:5432", restarts: 4

staging-westus2
  system-pool      ✅  CPU: 18%  |  Memory: 49%  |  Nodes: 2
  user-pool        ✅  CPU: 52%  |  Memory: 61%  |  Nodes: 8
  No failing pods.

prod-eastus
  system-pool      ✅  CPU: 31%  |  Memory: 58%  |  Nodes: 3
  user-pool        ✅  CPU: 67%  |  Memory: 71%  |  Nodes: 12
  No failing pods.

prod-westus2
  system-pool      ✅  CPU: 29%  |  Memory: 55%  |  Nodes: 3
  user-pool        ✅  CPU: 63%  |  Memory: 68%  |  Nodes: 12
  No failing pods.

⚠️  staging-eastus user-pool is 1 node from autoscaler ceiling (8/9 max).
    Consider reviewing before today's load test.
```

---

### B. Sam (On-Call Engineer) — 2am Incident Triage

```
Sam: "PagerDuty just fired for prod-eastus. What's happening?"

Claude: [calls check_node_pool_pressure(cluster="prod-eastus")]
        [calls get_pod_health(cluster="prod-eastus", status_filter="all", lookback_minutes=15)]
        [calls get_upgrade_progress(cluster="prod-eastus")]

🔴 prod-eastus snapshot — 02:14 UTC

Node pools:
  system-pool      ✅  CPU: 33%  |  Memory: 61%  |  Nodes: 3
  user-pool        🔴  CPU: 92%  |  Memory: 88%  |  Nodes: 12  (AT AUTOSCALER MAX)

Failing pods (last 15 min):
  Unschedulable (7) — namespace: payments, tenant: acme-corp
    "0/12 nodes available: Insufficient cpu."

No upgrade currently in progress.

Summary: user-pool is at autoscaler max (12/12). 7 payment pods for acme-corp
are unschedulable due to CPU exhaustion — not a platform failure, but the pool
has no more headroom. You may want to check if a quota increase or node pool
expansion is needed, or whether acme-corp has a runaway workload.
```

---

### C. Sam (On-Call Engineer) — Upgrade-Related Incident

```
Sam: "Tenants are reporting pod evictions in prod-eastus. Is anything platform-side happening?"

Claude: [calls get_upgrade_progress(cluster="prod-eastus")]

🔵 Upgrade in progress — prod-eastus / user-pool-general (1.29.8 → 1.30.5)
   Started: 34 minutes ago  |  Estimated remaining: ~36m

Node progress (12 total):
  ✅ Upgraded   5 nodes
  🟠 Cordoned   2 nodes  — actively draining (expected evictions)
  ⏳ Pending    5 nodes

Pod evictions are expected during a rolling node upgrade — cordoned nodes
drain workloads to other nodes before being upgraded. The 2 nodes currently
draining (aks-userpool-000006, 000007) will complete drain in approximately
4–6 minutes based on current pace. No stalled nodes detected.

Recommended tenant message: "The prod-eastus cluster is undergoing a planned
Kubernetes upgrade. Pod evictions in the next ~36 minutes are expected as nodes
are cycled. No action needed on your end."
```

---

### D. Jordan (Platform Tech Lead) — Weekly Version Review

```
Jordan: "What's our version posture across the fleet and do we have any
         upcoming end-of-support deadlines?"

Claude: [calls get_kubernetes_upgrade_status(cluster="all")]

Kubernetes version summary — fleet-wide

Cluster          Node Pool        Version   Support Status         Available Upgrades
dev-eastus       control-plane    1.28.9    ⛔ EOS in 3 weeks      1.29.9, 1.30.5
dev-eastus       system-pool      1.28.9    ⛔ EOS in 3 weeks      —
dev-eastus       user-pool        1.28.9    ⛔ EOS in 3 weeks      —
dev-westus2      control-plane    1.28.9    ⛔ EOS in 3 weeks      1.29.9, 1.30.5
dev-westus2      system-pool      1.28.9    ⛔ EOS in 3 weeks      —
dev-westus2      user-pool        1.28.9    ⛔ EOS in 3 weeks      —
staging-eastus   control-plane    1.29.8    ✅ Until Sep 2026       1.30.5
staging-eastus   system-pool      1.29.8    ✅ Until Sep 2026       —
staging-eastus   user-pool        1.29.8    ✅ Until Sep 2026       —
staging-westus2  control-plane    1.29.8    ✅ Until Sep 2026       1.30.5
staging-westus2  system-pool      1.29.8    ✅ Until Sep 2026       —
staging-westus2  user-pool        1.29.8    ✅ Until Sep 2026       —
prod-eastus      control-plane    1.29.8    ✅ Until Sep 2026       1.30.5
prod-eastus      system-pool      1.29.8    ✅ Until Sep 2026       —
prod-eastus      user-pool        1.29.8    ✅ Until Sep 2026       —
prod-westus2     control-plane    1.29.8    ✅ Until Sep 2026       1.30.5
prod-westus2     system-pool      1.29.8    ✅ Until Sep 2026       —
prod-westus2     user-pool        1.29.8    ✅ Until Sep 2026       —

⚠️  URGENT: Both dev clusters are 3 weeks from end-of-support on 1.28.x.
    Recommend scheduling eastus upgrade wave (dev → staging → prod) this week.
    Staging and prod are healthy — 1.30.5 is available when ready.
```

---

### E. Jordan (Platform Tech Lead) — Upgrade Window Sizing

```
Jordan: "I need to plan the prod-eastus upgrade maintenance window. How long should I
         expect the user-pool upgrade to take?"

Claude: [calls get_upgrade_duration_metrics(cluster="prod-eastus", node_pool="user-pool", history_count=4)]

Upgrade duration history — prod-eastus / user-pool (last 4 upgrades, sourced from AKS Activity Log)

Date         Version Upgrade    Duration   Nodes   Min/node   Max/node
2025-11-14   1.27.9 → 1.28.9   1h 04m     12      4m 51s     7m 22s
2025-08-01   1.26.6 → 1.27.9   58m        10      5m 10s     7m 47s
2025-05-10   1.25.5 → 1.26.6   52m        10      4m 37s     6m 52s
2025-02-20   1.24.9 → 1.25.5   57m        10      5m 02s     7m 01s

All 4 upgrades completed within the expected 60-minute window. ✅

Estimated for current planned upgrade (12 nodes, 1.29.8 → 1.30.5):
  Mean per-node (extrapolated): ~5m 10s × 12 = ~62m
  ⚠️  Slightly above 60-minute baseline — recommend a 90-minute window as buffer.
  No historical upgrades have approached 90 minutes on this pool.
```

---

### F. Alex (Platform Engineer) — Tenant Escalation

```
Alex: "Casey's team is saying payment-processor pods keep restarting in prod-eastus.
       Can you pull what's happening?"

Claude: [calls get_pod_health(cluster="prod-eastus", namespace="payments", lookback_minutes=60)]

Pod health — prod-eastus / payments namespace (last 60 min)

🔴 OOMKilled (4 pods)
  payment-processor-7d9f   OOMKilled   container: app   limit: 512Mi   restarts: 11
  payment-processor-4c2a   OOMKilled   container: app   limit: 512Mi   restarts: 9
  payment-processor-1b8e   OOMKilled   container: app   limit: 512Mi   restarts: 7
  payment-processor-9f3a   OOMKilled   container: app   limit: 512Mi   restarts: 6

Last event (representative): "OOMKilling container app in pod payment-processor-7d9f:
Container app exceeded its memory limit of 512Mi."

This is a tenant-side configuration issue, not a platform failure. All 4 pods
are repeatedly hitting the 512Mi memory limit. The restart counts (6–11) suggest
this has been ongoing for some time. Recommend Casey's team review their memory
limit — if actual usage exceeds 512Mi, the limit should be increased.
```

---

### G. Alex (Platform Engineer) — PDB Preflight Before Upgrade

```
Alex: "We're about to kick off the prod-eastus user-pool upgrade through the ADO pipeline.
       Anything that could block drain?"

Claude: [calls check_pdb_upgrade_risk(cluster="prod-eastus", node_pool="user-pool", mode="preflight")]

PDB preflight check — prod-eastus / user-pool

⚠️  2 PDBs may block node drain:

  payments / payment-processor-pdb
    Rule: minAvailable=3  |  Current ready pods: 3  |  Disruptable: 0
    ⚠️  No disruptions currently allowed. Any eviction will be blocked
        until a new pod becomes ready elsewhere.

  data-pipeline / spark-coordinator-pdb
    Rule: maxUnavailable=0  |  Current pods: 2  |  Disruptable: 0
    ⚠️  maxUnavailable=0 means drain will always be blocked for this workload.

✅ Remaining 47 PDBs across prod-eastus have available disruption budget.

Recommendation: Coordinate with the payments and data-pipeline tenant teams
before triggering the upgrade. The payments PDB may self-resolve if the tenant
has spare replicas elsewhere; the spark-coordinator PDB with maxUnavailable=0
will require the tenant to temporarily relax the budget or scale up during the
upgrade window.
```

---

### H. Sam (On-Call Engineer) — PDB Blocking Live Upgrade

```
Sam: "The prod-eastus upgrade has been running for 75 minutes. Something is stuck."

Claude: [calls get_upgrade_progress(cluster="prod-eastus")]
        [calls check_pdb_upgrade_risk(cluster="prod-eastus", node_pool="user-pool", mode="live")]

🔴 Upgrade running 75 minutes — exceeds expected 60-minute window

Node progress (12 total):
  ✅ Upgraded    10 nodes
  🚫 PDB Blocked  1 node   aks-userpool-000011 — cordoned 38 minutes, drain blocked
  ⏳ Pending      1 node   aks-userpool-000012

Live PDB block detail:
  Node: aks-userpool-000011 (cordoned 38m ago)
  Blocking PDB: payments / payment-processor-pdb
    minAvailable=3  |  Ready pods: 3  |  Disruptable: 0
    Pod pending eviction: payment-processor-4c2a
    Block duration: 38 minutes

The upgrade is not stalled — it is actively blocked by a PDB. The payment-processor
PDB has minAvailable=3 and there are exactly 3 ready replicas cluster-wide, so no
eviction is possible until a new replica becomes ready on another node.

Options:
  1. Ask the payments team to temporarily scale to 4+ replicas to create disruption budget
  2. Ask them to relax minAvailable to 2 during the upgrade window
  3. Escalate to the payments team and wait — once a pod reschedules successfully
     the drain will proceed automatically
```
