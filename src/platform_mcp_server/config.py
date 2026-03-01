"""Cluster configuration, thresholds, and environment variable overrides."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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


_REQUIRED_FIELDS = (
    "environment",
    "region",
    "subscription_id",
    "resource_group",
    "aks_cluster_name",
    "kubeconfig_context",
)


def _load_cluster_map(path: Path) -> dict[str, ClusterConfig]:
    """Parse a YAML cluster configuration file and return a mapping of cluster ID to ClusterConfig.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A dict mapping cluster IDs to ClusterConfig objects.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If the file content is malformed or missing required fields.
    """
    if not path.exists():
        msg = (
            f"Cluster configuration file not found: {path}. "
            "Copy clusters.example.yaml to clusters.yaml and fill in your subscription IDs, "
            "or set PLATFORM_MCP_CLUSTERS to point to your config file."
        )
        raise FileNotFoundError(msg)

    raw = yaml.safe_load(path.read_text())

    if not isinstance(raw, dict) or "clusters" not in raw:
        msg = f"Cluster config file {path} must contain a top-level 'clusters' key."
        raise ValueError(msg)

    clusters_raw: Any = raw["clusters"]
    if not isinstance(clusters_raw, dict) or len(clusters_raw) == 0:
        msg = f"Cluster config file {path} has an empty or invalid 'clusters' section."
        raise ValueError(msg)

    cluster_map: dict[str, ClusterConfig] = {}
    for cluster_id, entry in clusters_raw.items():
        if not isinstance(entry, dict):
            msg = f"Cluster '{cluster_id}' must be a mapping, got {type(entry).__name__}."
            raise ValueError(msg)

        missing = [f for f in _REQUIRED_FIELDS if f not in entry]
        if missing:
            msg = f"Cluster '{cluster_id}' is missing required fields: {', '.join(missing)}."
            raise ValueError(msg)

        cluster_map[cluster_id] = ClusterConfig(
            cluster_id=cluster_id,
            environment=str(entry["environment"]),
            region=str(entry["region"]),
            subscription_id=str(entry["subscription_id"]),
            resource_group=str(entry["resource_group"]),
            aks_cluster_name=str(entry["aks_cluster_name"]),
            kubeconfig_context=str(entry["kubeconfig_context"]),
        )

    return cluster_map


CLUSTER_MAP: dict[str, ClusterConfig] = {}
ALL_CLUSTER_IDS: list[str] = []


def load_cluster_map() -> dict[str, ClusterConfig]:
    """Load cluster configuration from YAML and populate module-level globals.

    Reads the file path from the ``PLATFORM_MCP_CLUSTERS`` environment variable,
    defaulting to ``clusters.yaml`` in the current working directory.

    Returns:
        The loaded cluster map.
    """
    path = Path(os.environ.get("PLATFORM_MCP_CLUSTERS", "clusters.yaml"))
    loaded = _load_cluster_map(path)
    CLUSTER_MAP.clear()
    CLUSTER_MAP.update(loaded)
    ALL_CLUSTER_IDS.clear()
    ALL_CLUSTER_IDS.extend(loaded.keys())
    return CLUSTER_MAP


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


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def validate_cluster_config() -> None:
    """Validate all cluster configurations at startup.

    Raises RuntimeError if placeholder subscription IDs, invalid UUID formats,
    or empty required fields are detected.
    """
    errors: list[str] = []
    for cluster_id, config in CLUSTER_MAP.items():
        if config.subscription_id.startswith("<") and config.subscription_id.endswith(">"):
            errors.append(f"{cluster_id}: placeholder subscription_id detected")
        elif not _UUID_RE.match(config.subscription_id):
            errors.append(f"{cluster_id}: subscription_id is not a valid UUID")

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
    return ThresholdConfig()
