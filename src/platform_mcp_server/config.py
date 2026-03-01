"""Cluster configuration, thresholds, and environment variable overrides."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# Note 1: `Literal` from typing lets you declare a type as exactly one of a fixed set of string
# Note 2: values. This acts like a closed enum without needing a full Enum class -- the type
# Note 3: checker will reject any string that is not listed, giving you compile-time safety.
from typing import Literal

# Note 4: `Literal[...]` defines ClusterID as a union of the six exact strings shown below.
# Note 5: Functions that accept a ClusterID parameter signal to callers that only these six
# Note 6: strings are meaningful -- anything else is a programming error, not a runtime condition.
ClusterID = Literal[
    "dev-eastus",
    "dev-westus2",
    "staging-eastus",
    "staging-westus2",
    "prod-eastus",
    "prod-westus2",
]

# Note 7: ALL_CLUSTER_IDS duplicates the Literal values as a plain list so runtime code can
# Note 8: iterate, validate, or display them without relying on typing internals (which are
# Note 9: not designed for runtime introspection in older Python versions).
ALL_CLUSTER_IDS: list[str] = [
    "dev-eastus",
    "dev-westus2",
    "staging-eastus",
    "staging-westus2",
    "prod-eastus",
    "prod-westus2",
]


# Note 10: `@dataclass(frozen=True)` generates __init__, __repr__, and __eq__ automatically,
# Note 11: but also makes every field read-only after construction by overriding __setattr__
# Note 12: and __delattr__ to raise FrozenInstanceError. This prevents accidental mutation of
# Note 13: configuration objects that are shared across multiple call sites.
@dataclass(frozen=True)
class ClusterConfig:
    """Configuration for a single AKS cluster."""

    # Note 14: The composite cluster_id (e.g., "prod-eastus") encodes both environment and region
    # Note 15: in one string. A single field is far more convenient as a dict key, log tag, or URL
    # Note 16: segment than two separate fields that callers would have to join themselves.
    cluster_id: str
    environment: str
    region: str
    subscription_id: str
    resource_group: str
    aks_cluster_name: str
    kubeconfig_context: str


# Note 17: ThresholdConfig also uses frozen=True so that a single shared instance returned by
# Note 18: get_thresholds() cannot be silently mutated by any consumer -- every caller sees the
# Note 19: same values that were read from the environment at instantiation time.
@dataclass(frozen=True)
class ThresholdConfig:
    """Operational thresholds with environment variable overrides."""

    # Note 20: `field(default_factory=lambda: ...)` defers evaluation of the default value until
    # Note 21: the dataclass is instantiated, NOT when the module is imported. This matters because
    # Note 22: environment variables set after import time (e.g., in tests or Docker entrypoints)
    # Note 23: are picked up correctly. A bare `default=float(os.environ.get(...))` would bake in
    # Note 24: whatever the env var was at import time and ignore later changes.
    cpu_warning: float = field(default_factory=lambda: float(os.environ.get("PRESSURE_CPU_WARNING", "75")))
    # Note 25: `os.environ.get(key, default)` returns the default string when the variable is
    # Note 26: absent, unlike `os.environ[key]` which raises KeyError. Wrapping with float() / int()
    # Note 27: converts the string to the correct numeric type for threshold comparisons.
    cpu_critical: float = field(default_factory=lambda: float(os.environ.get("PRESSURE_CPU_CRITICAL", "90")))
    memory_warning: float = field(default_factory=lambda: float(os.environ.get("PRESSURE_MEMORY_WARNING", "80")))
    memory_critical: float = field(default_factory=lambda: float(os.environ.get("PRESSURE_MEMORY_CRITICAL", "95")))
    pending_pods_warning: int = field(default_factory=lambda: int(os.environ.get("PRESSURE_PENDING_PODS_WARNING", "1")))
    pending_pods_critical: int = field(
        default_factory=lambda: int(os.environ.get("PRESSURE_PENDING_PODS_CRITICAL", "10"))
    )
    upgrade_anomaly_minutes: int = field(default_factory=lambda: int(os.environ.get("UPGRADE_ANOMALY_MINUTES", "60")))


# Cluster configuration mapping — single source of truth for cluster resolution.
# Values are placeholders; engineers override via environment or config file per deployment.
# Note 28: CLUSTER_MAP is the canonical registry of every cluster this server knows about.
# Note 29: Centralising all six entries here means adding a new cluster is a single-file change
# Note 30: with no logic to update -- resolve_cluster and validate_cluster_config work automatically.
CLUSTER_MAP: dict[str, ClusterConfig] = {
    "dev-eastus": ClusterConfig(
        cluster_id="dev-eastus",
        environment="dev",
        region="eastus",
        # Note 31: Angle-bracket placeholders like "<dev-subscription-id>" are a deliberate sentinel
        # Note 32: value. They are visually obvious in logs and are easy to detect programmatically
        # Note 33: (starts with "<", ends with ">") without needing a separate "unset" sentinel type.
        subscription_id="<dev-subscription-id>",
        resource_group="rg-dev-eastus",
        aks_cluster_name="aks-dev-eastus",
        kubeconfig_context="aks-dev-eastus",
    ),
    "dev-westus2": ClusterConfig(
        cluster_id="dev-westus2",
        environment="dev",
        region="westus2",
        subscription_id="<dev-subscription-id>",
        resource_group="rg-dev-westus2",
        aks_cluster_name="aks-dev-westus2",
        kubeconfig_context="aks-dev-westus2",
    ),
    "staging-eastus": ClusterConfig(
        cluster_id="staging-eastus",
        environment="staging",
        region="eastus",
        subscription_id="<staging-subscription-id>",
        resource_group="rg-staging-eastus",
        aks_cluster_name="aks-staging-eastus",
        kubeconfig_context="aks-staging-eastus",
    ),
    "staging-westus2": ClusterConfig(
        cluster_id="staging-westus2",
        environment="staging",
        region="westus2",
        subscription_id="<staging-subscription-id>",
        resource_group="rg-staging-westus2",
        aks_cluster_name="aks-staging-westus2",
        kubeconfig_context="aks-staging-westus2",
    ),
    "prod-eastus": ClusterConfig(
        cluster_id="prod-eastus",
        environment="prod",
        region="eastus",
        subscription_id="<prod-subscription-id>",
        resource_group="rg-prod-eastus",
        aks_cluster_name="aks-prod-eastus",
        kubeconfig_context="aks-prod-eastus",
    ),
    "prod-westus2": ClusterConfig(
        cluster_id="prod-westus2",
        environment="prod",
        region="westus2",
        subscription_id="<prod-subscription-id>",
        resource_group="rg-prod-westus2",
        aks_cluster_name="aks-prod-westus2",
        kubeconfig_context="aks-prod-westus2",
    ),
}


def resolve_cluster(cluster_id: str) -> ClusterConfig:
    """Resolve a composite cluster ID to its full configuration.

    Args:
        cluster_id: One of the valid composite cluster IDs (e.g., 'prod-eastus').

    Returns:
        The ClusterConfig for the given cluster.

    Raises:
        ValueError: If the cluster_id is not found in CLUSTER_MAP.
    """
    # Note 34: Checking membership before accessing avoids a raw KeyError, which has a less
    # Note 35: helpful message. Raising ValueError with a list of valid choices helps callers
    # Note 36: self-diagnose typos without reading source code.
    if cluster_id not in CLUSTER_MAP:
        valid = ", ".join(sorted(CLUSTER_MAP.keys()))
        msg = f"Unknown cluster '{cluster_id}'. Valid clusters: {valid}"
        raise ValueError(msg)
    return CLUSTER_MAP[cluster_id]


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def validate_cluster_config() -> None:
    """Validate all cluster configurations at startup.

    Raises RuntimeError if placeholder subscription IDs, invalid UUID formats,
    or empty required fields are detected.
    """
    errors: list[str] = []
    for cluster_id, config in CLUSTER_MAP.items():
        # Check subscription_id is a valid UUID (not a placeholder or garbage)
        if config.subscription_id.startswith("<") and config.subscription_id.endswith(">"):
            errors.append(f"{cluster_id}: placeholder subscription_id detected")
        elif not _UUID_RE.match(config.subscription_id):
            errors.append(f"{cluster_id}: subscription_id is not a valid UUID")

        # Check required string fields are non-empty
        if not config.resource_group:
            errors.append(f"{cluster_id}: resource_group is empty")
        if not config.aks_cluster_name:
            errors.append(f"{cluster_id}: aks_cluster_name is empty")
        if not config.kubeconfig_context:
            errors.append(f"{cluster_id}: kubeconfig_context is empty")

    if errors:
        detail = "; ".join(errors)
        msg = f"Cluster configuration errors: {detail}. Fix before running in production."
        raise RuntimeError(msg)


def get_thresholds() -> ThresholdConfig:
    """Return threshold configuration with environment variable overrides applied."""
    # Note 44: Constructing ThresholdConfig() here (rather than at module level) means each call
    # Note 45: re-reads the environment. This is intentional: tests can patch os.environ between
    # Note 46: calls without reloading the module, and the returned object is frozen so it cannot
    # Note 47: be mutated after the read -- every caller gets a consistent, immutable snapshot.
    return ThresholdConfig()
