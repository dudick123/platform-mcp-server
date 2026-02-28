"""Pydantic v2 models for all tool inputs, outputs, and errors."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

VALID_CLUSTERS = Literal[
    "dev-eastus",
    "dev-westus2",
    "staging-eastus",
    "staging-westus2",
    "prod-eastus",
    "prod-westus2",
    "all",
]


# --- Shared error model ---


class ToolError(BaseModel):
    """Structured error model returned by all tools."""

    error: str
    source: str
    cluster: str
    partial_data: bool = False


# --- Output scrubbing ---

_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_SUBSCRIPTION_PATTERN = re.compile(r"/subscriptions/[a-f0-9-]+", re.IGNORECASE)
_RESOURCE_GROUP_PATTERN = re.compile(r"/resourceGroups/[^/]+", re.IGNORECASE)
_FQDN_PATTERN = re.compile(r"\b[\w.-]+\.azmk8s\.io\b", re.IGNORECASE)
_AZURE_HOST_PATTERN = re.compile(r"\b[\w.-]+\.(vault\.azure\.net|blob\.core\.windows\.net)\b", re.IGNORECASE)


def scrub_sensitive_values(text: str) -> str:
    """Remove internal IPs, subscription IDs, resource group names, and Azure FQDNs from text.

    Node names (e.g., aks-userpool-00000001) are preserved.
    """
    if not text:
        return text
    result = _IP_PATTERN.sub("[REDACTED_IP]", text)
    result = _RESOURCE_GROUP_PATTERN.sub("/resourceGroups/[REDACTED]", result)
    result = _SUBSCRIPTION_PATTERN.sub("/subscriptions/[REDACTED]", result)
    result = _FQDN_PATTERN.sub("[REDACTED_FQDN]", result)
    result = _AZURE_HOST_PATTERN.sub("[REDACTED_HOST]", result)
    return result


# --- Node Pool Pressure models ---


class NodePoolPressureInput(BaseModel):
    """Input parameters for check_node_pool_pressure."""

    cluster: VALID_CLUSTERS


class NodePoolResult(BaseModel):
    """Pressure data for a single node pool."""

    pool_name: str
    cpu_requests_percent: float | None = None
    memory_requests_percent: float | None = None
    pending_pods: int
    ready_nodes: int
    max_nodes: int | None = None
    pressure_level: Literal["ok", "warning", "critical"]


class NodePoolPressureOutput(BaseModel):
    """Output for check_node_pool_pressure."""

    cluster: str
    pools: list[NodePoolResult]
    summary: str
    timestamp: str
    errors: list[ToolError] = Field(default_factory=list)


# --- Pod Health models ---


class PodDetail(BaseModel):
    """Detail for a single unhealthy pod."""

    name: str
    namespace: str
    phase: str
    reason: str | None = None
    failure_category: str | None = None
    restart_count: int = 0
    last_event: str | None = None
    container_name: str | None = None
    memory_limit: str | None = None


class PodHealthInput(BaseModel):
    """Input parameters for get_pod_health."""

    cluster: VALID_CLUSTERS
    namespace: str | None = None
    status_filter: Literal["pending", "failed", "all"] = "all"
    lookback_minutes: int = Field(default=30, ge=1, le=1440)


class PodHealthOutput(BaseModel):
    """Output for get_pod_health."""

    cluster: str
    pods: list[PodDetail]
    groups: dict[str, int]
    total_matching: int
    truncated: bool
    summary: str
    timestamp: str
    errors: list[ToolError] = Field(default_factory=list)


# --- Upgrade Status models ---


class NodePoolVersionInfo(BaseModel):
    """Version info for a single node pool."""

    pool_name: str
    current_version: str
    target_version: str | None = None
    upgrading: bool = False
    support_status: str | None = None
    days_until_eol: int | None = None


class UpgradeStatusInput(BaseModel):
    """Input parameters for get_kubernetes_upgrade_status."""

    cluster: VALID_CLUSTERS


class UpgradeStatusOutput(BaseModel):
    """Output for get_kubernetes_upgrade_status."""

    cluster: str
    control_plane_version: str
    node_pools: list[NodePoolVersionInfo]
    available_upgrades: list[str]
    upgrade_active: bool
    summary: str
    timestamp: str
    errors: list[ToolError] = Field(default_factory=list)


# --- Upgrade Progress models ---


class NodeUpgradeState(BaseModel):
    """State of a single node during an upgrade."""

    name: str
    state: Literal["upgraded", "upgrading", "cordoned", "pdb_blocked", "pending", "stalled"]
    version: str
    time_in_state_seconds: float | None = None
    blocking_pdb: str | None = None
    blocking_pdb_namespace: str | None = None


class UpgradeProgressInput(BaseModel):
    """Input parameters for get_upgrade_progress."""

    cluster: VALID_CLUSTERS
    node_pool: str | None = None


class UpgradeProgressOutput(BaseModel):
    """Output for get_upgrade_progress."""

    cluster: str
    upgrade_in_progress: bool
    node_pool: str | None = None
    target_version: str | None = None
    nodes: list[NodeUpgradeState]
    nodes_total: int | None = None
    nodes_upgraded: int | None = None
    nodes_remaining: int | None = None
    elapsed_seconds: float | None = None
    estimated_remaining_seconds: float | None = None
    anomaly_flag: str | None = None
    summary: str
    timestamp: str
    errors: list[ToolError] = Field(default_factory=list)


# --- Upgrade Duration Metrics models ---


class CurrentRunMetrics(BaseModel):
    """Timing metrics for the current in-progress upgrade."""

    elapsed_seconds: float
    estimated_remaining_seconds: float | None = None
    nodes_completed: int
    nodes_total: int
    mean_seconds_per_node: float
    slowest_node: str | None = None
    fastest_node: str | None = None


class HistoricalUpgradeRecord(BaseModel):
    """A single historical upgrade duration record from AKS Activity Log."""

    date: str
    version_path: str
    total_duration_seconds: float
    node_count: int
    min_per_node_seconds: float
    max_per_node_seconds: float


class HistoricalStats(BaseModel):
    """Statistical summary of historical upgrade durations."""

    mean_duration_seconds: float
    p90_duration_seconds: float
    all_within_baseline: bool


class UpgradeDurationInput(BaseModel):
    """Input parameters for get_upgrade_duration_metrics."""

    cluster: VALID_CLUSTERS
    node_pool: str
    history_count: int = Field(default=5, ge=1, le=50)


class UpgradeDurationOutput(BaseModel):
    """Output for get_upgrade_duration_metrics."""

    cluster: str
    node_pool: str
    current_run: CurrentRunMetrics | None = None
    historical: list[HistoricalUpgradeRecord]
    stats: HistoricalStats | None = None
    anomaly_flag: str | None = None
    summary: str
    timestamp: str
    errors: list[ToolError] = Field(default_factory=list)


# --- PDB Check models ---


class PdbRisk(BaseModel):
    """A single PDB that poses upgrade risk."""

    pdb_name: str
    namespace: str
    workload: str
    reason: str
    affected_pods: int
    affected_nodes: list[str] | None = None
    block_duration_seconds: float | None = None


class PdbCheckInput(BaseModel):
    """Input parameters for check_pdb_upgrade_risk."""

    cluster: VALID_CLUSTERS
    node_pool: str | None = None
    mode: Literal["preflight", "live"] = "preflight"


class PdbCheckOutput(BaseModel):
    """Output for check_pdb_upgrade_risk."""

    cluster: str
    mode: str
    risks: list[PdbRisk]
    summary: str
    timestamp: str
    errors: list[ToolError] = Field(default_factory=list)
