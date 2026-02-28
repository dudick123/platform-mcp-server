"""Cluster configuration, thresholds, and environment variable overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

ClusterID = Literal[
    "dev-eastus",
    "dev-westus2",
    "staging-eastus",
    "staging-westus2",
    "prod-eastus",
    "prod-westus2",
]

ALL_CLUSTER_IDS: list[str] = [
    "dev-eastus",
    "dev-westus2",
    "staging-eastus",
    "staging-westus2",
    "prod-eastus",
    "prod-westus2",
]


@dataclass(frozen=True)
class ClusterConfig:
    """Configuration for a single AKS cluster."""

    cluster_id: str
    environment: str
    region: str
    subscription_id: str
    resource_group: str
    aks_cluster_name: str
    kubeconfig_context: str


@dataclass(frozen=True)
class ThresholdConfig:
    """Operational thresholds with environment variable overrides."""

    cpu_warning: float = field(default_factory=lambda: float(os.environ.get("PRESSURE_CPU_WARNING", "75")))
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
CLUSTER_MAP: dict[str, ClusterConfig] = {
    "dev-eastus": ClusterConfig(
        cluster_id="dev-eastus",
        environment="dev",
        region="eastus",
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
    if cluster_id not in CLUSTER_MAP:
        valid = ", ".join(sorted(CLUSTER_MAP.keys()))
        msg = f"Unknown cluster '{cluster_id}'. Valid clusters: {valid}"
        raise ValueError(msg)
    return CLUSTER_MAP[cluster_id]


def validate_cluster_config() -> None:
    """Validate all cluster configurations at startup.

    Raises RuntimeError if placeholder subscription IDs are detected.
    """
    placeholder_clusters = []
    for cluster_id, config in CLUSTER_MAP.items():
        if config.subscription_id.startswith("<") and config.subscription_id.endswith(">"):
            placeholder_clusters.append(cluster_id)
    if placeholder_clusters:
        clusters = ", ".join(placeholder_clusters)
        msg = (
            f"Placeholder subscription IDs detected for clusters: {clusters}. "
            f"Set real subscription IDs before running in production."
        )
        raise RuntimeError(msg)


def get_thresholds() -> ThresholdConfig:
    """Return threshold configuration with environment variable overrides applied."""
    return ThresholdConfig()
