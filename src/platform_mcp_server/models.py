"""Pydantic v2 models for all tool inputs, outputs, and errors."""

# Note 1: `from __future__ import annotations` switches Python to PEP 563 "postponed
# Note 2: evaluation" mode. Annotation expressions are stored as strings instead of
# Note 3: being evaluated at import time. This lets you reference a class before it is
# Note 4: defined in the same file (a forward reference), and it avoids circular-import
# Note 5: errors when two modules reference each other's types.
from __future__ import annotations

import re

# Note 6: `Literal` is used to define a closed set of allowed string values directly in
# Note 7: the type system. The type checker (and Pydantic) will reject any value not
# Note 8: listed, giving you enumeration behaviour without the overhead of a full Enum.
from typing import Literal

# Note 9: Pydantic v2 `BaseModel` provides automatic data validation, type coercion,
# Note 10: and JSON serialisation from a single class definition. Declare fields as
# Note 11: annotated attributes and Pydantic generates __init__, validation, and a JSON
# Note 12: schema for free. `Field` lets you attach metadata like defaults and validators.
from pydantic import BaseModel, Field

# Note 13: `Literal` is preferred over `Enum` here because the valid cluster names are
# Note 14: a small, fixed set that never needs iteration or numeric values. A `Literal`
# Note 15: union is also understood natively by Pydantic and type checkers without any
# Note 16: extra boilerplate. Use `Enum` when you need methods, ordering, or iteration.
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


# Note 17: Every tool returns this model on failure so callers always receive a
# Note 18: consistent shape: they can check for the `error` key and know exactly
# Note 19: which cluster and source produced the problem.
class ToolError(BaseModel):
    """Structured error returned by all tools."""

    error: str
    source: str
    cluster: str
    # Note 20: `partial_data: bool = False` shows how Pydantic handles field defaults.
    # Note 21: When a field has a plain immutable default (bool, int, str, None) you
    # Note 22: can assign it directly. Pydantic stores the value and uses it whenever
    # Note 23: the field is omitted from the constructor -- no Field() wrapper needed.
    partial_data: bool = False


# --- Output scrubbing ---

# Note 24: All five patterns are compiled at module scope with `re.compile()`. Compiling
# Note 25: once converts the regex string into a finite automaton (a DFA/NFA state
# Note 26: machine) that is reused for every call to `.sub()` or `.match()`. Compiling
# Note 27: inside the function would repeat that work on every invocation.
# Note 28: The IP pattern uses `\b` word-boundary anchors so it matches only complete
# Note 29: dotted-quad addresses. `\d{1,3}` allows 1-3 digits per octet, which covers
# Note 30: the full range 0-255 without a complex numeric range assertion.
_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
# Note 31: The subscription pattern matches the Azure ARM path segment
# Note 32: `/subscriptions/<guid>`. The character class `[a-f0-9-]` covers lowercase
# Note 33: hex digits and hyphens that form a UUID. `re.IGNORECASE` is applied because
# Note 34: Azure resource paths are case-insensitive in the REST API responses.
_SUBSCRIPTION_PATTERN = re.compile(r"/subscriptions/[a-f0-9-]+", re.IGNORECASE)
# Note 35: The resource group pattern captures `/resourceGroups/<name>` where the name
# Note 36: can be any non-slash characters. This prevents leaking internal group names
# Note 37: that may encode environment topology or team ownership information.
_RESOURCE_GROUP_PATTERN = re.compile(r"/resourceGroups/[^/]+", re.IGNORECASE)
# Note 38: The FQDN pattern targets AKS API server hostnames ending in `.azmk8s.io`.
# Note 39: `[\w.-]+` matches any subdomain labels. Redacting these prevents exposing
# Note 40: the direct API server endpoint, which is a lateral-movement target.
_FQDN_PATTERN = re.compile(r"\b[\w.-]+\.azmk8s\.io\b", re.IGNORECASE)
# Note 41: The Azure host pattern covers Key Vault and Blob Storage FQDNs. Leaking
# Note 42: storage account or vault hostnames can reveal infrastructure naming schemes
# Note 43: and provide a starting point for unauthenticated probing.
_AZURE_HOST_PATTERN = re.compile(r"\b[\w.-]+\.(vault\.azure\.net|blob\.core\.windows\.net)\b", re.IGNORECASE)


def scrub_sensitive_values(text: str) -> str:
    """Remove internal IPs, subscription IDs, resource group names, and Azure FQDNs from text.

    Node names (e.g., aks-userpool-00000001) are preserved.
    """
    if not text:
        return text
    # Note 44: The substitution order is deliberate. Resource group is replaced before
    # Note 45: subscription so that a combined path like
    # Note 46: `/subscriptions/<id>/resourceGroups/<name>/...` is processed correctly:
    # Note 47: the inner segment is redacted first, leaving the subscription prefix
    # Note 48: intact for the second pass. Reversing the order would leave a dangling
    # Note 49: `/resourceGroups/<name>` after the subscription prefix is replaced.
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
    # Note 50: `float | None` is the Python 3.10+ union syntax and is exactly equivalent
    # Note 51: to `Optional[float]` from the `typing` module. Both expand to
    # Note 52: `Union[float, None]` at runtime. The `|` form is preferred in modern
    # Note 53: code because it reads naturally as "float or None" without an extra import.
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
    # Note 54: `Field(default_factory=list)` is required because `list` is a mutable
    # Note 55: type. If you wrote `errors: list[ToolError] = []`, Python would share
    # Note 56: the same list object across every model instance, causing mutations in
    # Note 57: one instance to silently affect all others. A `default_factory` is
    # Note 58: called fresh for each new instance, guaranteeing isolation.
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
    # Note 59: `Field(ge=1, le=1440)` attaches built-in Pydantic validators that enforce
    # Note 60: a numeric range without any manual if-raise guard. `ge` means
    # Note 61: "greater than or equal to" and `le` means "less than or equal to".
    # Note 62: Pydantic raises a `ValidationError` automatically if the value falls
    # Note 63: outside [1, 1440], keeping validation logic out of business code.
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
    # Note 64: The six states model the full AKS node upgrade lifecycle:
    # Note 65:   "upgraded"   -- node is running the target version and is schedulable.
    # Note 66:   "upgrading"  -- AKS is actively re-imaging this node.
    # Note 67:   "cordoned"   -- node is marked unschedulable; drain has begun.
    # Note 68:   "pdb_blocked"-- eviction is stalled because a PodDisruptionBudget
    # Note 69:                   would be violated; the node cannot be drained yet.
    # Note 70:   "pending"    -- node is queued but the upgrade wave has not reached it.
    # Note 71:   "stalled"    -- node has been in a non-terminal state longer than the
    # Note 72:                   expected threshold, indicating a possible hang.
    state: Literal["upgraded", "upgrading", "cordoned", "pdb_blocked", "pending", "stalled"]
    version: str
    time_in_state_seconds: float | None = None
    blocking_pdb: str | None = None
    blocking_pdb_namespace: str | None = None


class UpgradeProgressInput(BaseModel):
    """Input parameters for get_upgrade_progress."""

    cluster: VALID_CLUSTERS
    node_pool: str | None = None


class AffectedPod(BaseModel):
    """A pod affected by an upgrade-related node transition."""

    name: str
    namespace: str
    phase: str
    reason: str | None = None
    node_name: str | None = None


class PodTransitionSummary(BaseModel):
    """Summary of pod transitions during an upgrade."""

    pending_count: int = 0
    failed_count: int = 0
    # Note 73: Both `by_category` and `affected_pods` use `default_factory` because
    # Note 74: `dict` and `list` are mutable. Assigning `= {}` or `= []` directly
    # Note 75: would create a single shared object reused by every model instance.
    # Note 76: `default_factory=dict` (and `default_factory=list`) ensure each new
    # Note 77: `PodTransitionSummary` instance gets its own independent container.
    by_category: dict[str, int] = Field(default_factory=dict)
    affected_pods: list[AffectedPod] = Field(default_factory=list)
    total_affected: int = 0


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
    pod_transitions: PodTransitionSummary | None = None
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
    # Note 78: `p90_duration_seconds` stores the 90th-percentile upgrade duration.
    # Note 79: P90 means that 90% of observed upgrades completed within this many
    # Note 80: seconds. It is more robust than the mean for SLO comparisons because
    # Note 81: a single very slow upgrade inflates the mean but barely moves P90,
    # Note 82: giving a better picture of typical worst-case behaviour.
    p90_duration_seconds: float
    all_within_baseline: bool


class UpgradeDurationInput(BaseModel):
    """Input parameters for get_upgrade_duration_metrics."""

    cluster: VALID_CLUSTERS
    node_pool: str
    # Note 83: `Field(ge=1, le=50)` constrains `history_count` to [1, 50] using
    # Note 84: Pydantic's built-in numeric validators. This eliminates the need for
    # Note 85: a manual range check inside the tool handler and ensures invalid
    # Note 86: requests are rejected at the model boundary before any I/O occurs.
    history_count: int = Field(default=5, ge=1, le=50)


class UpgradeDurationOutput(BaseModel):
    """Output for get_upgrade_duration_metrics."""

    cluster: str
    node_pool: str
    current_run: CurrentRunMetrics | None = None
    historical: list[HistoricalUpgradeRecord]
    stats: HistoricalStats | None = None
    anomaly_flag: str | None = None
    # Note 87: `model_dump_json()` is Pydantic v2's built-in serialiser. It converts
    # Note 88: the model to a JSON string without needing `json.dumps(model.dict())`.
    # Note 89: It respects field aliases, custom serialisers, and exclusion rules
    # Note 90: defined on the model, making it the preferred way to emit JSON output.
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
