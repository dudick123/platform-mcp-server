"""Tests for config.py: cluster mapping, thresholds, environment variable overrides."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from platform_mcp_server.config import (
    ALL_CLUSTER_IDS,
    CLUSTER_MAP,
    ClusterConfig,
    ThresholdConfig,
    get_thresholds,
    resolve_cluster,
)


class TestClusterMap:
    """Tests for the CLUSTER_MAP configuration."""

    def test_cluster_map_has_six_entries(self) -> None:
        assert len(CLUSTER_MAP) == 6

    def test_all_cluster_ids_match_map_keys(self) -> None:
        assert set(ALL_CLUSTER_IDS) == set(CLUSTER_MAP.keys())

    @pytest.mark.parametrize(
        "cluster_id,expected_env,expected_region",
        [
            ("dev-eastus", "dev", "eastus"),
            ("dev-westus2", "dev", "westus2"),
            ("staging-eastus", "staging", "eastus"),
            ("staging-westus2", "staging", "westus2"),
            ("prod-eastus", "prod", "eastus"),
            ("prod-westus2", "prod", "westus2"),
        ],
    )
    def test_cluster_config_environment_and_region(
        self, cluster_id: str, expected_env: str, expected_region: str
    ) -> None:
        config = CLUSTER_MAP[cluster_id]
        assert config.environment == expected_env
        assert config.region == expected_region
        assert config.cluster_id == cluster_id

    def test_each_cluster_has_unique_kubeconfig_context(self) -> None:
        contexts = [c.kubeconfig_context for c in CLUSTER_MAP.values()]
        assert len(contexts) == len(set(contexts))

    def test_cluster_config_is_frozen(self) -> None:
        config = CLUSTER_MAP["dev-eastus"]
        with pytest.raises(AttributeError):
            config.cluster_id = "other"  # type: ignore[misc]


class TestResolveCluster:
    """Tests for the resolve_cluster function."""

    def test_resolve_valid_cluster(self) -> None:
        config = resolve_cluster("prod-eastus")
        assert isinstance(config, ClusterConfig)
        assert config.cluster_id == "prod-eastus"
        assert config.environment == "prod"
        assert config.region == "eastus"

    def test_resolve_invalid_cluster_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown cluster 'nonexistent'"):
            resolve_cluster("nonexistent")

    def test_resolve_invalid_cluster_lists_valid_ids(self) -> None:
        with pytest.raises(ValueError, match="dev-eastus") as exc_info:
            resolve_cluster("bad-cluster")
        error_msg = str(exc_info.value)
        for cluster_id in ALL_CLUSTER_IDS:
            assert cluster_id in error_msg


class TestThresholdConfig:
    """Tests for ThresholdConfig defaults and environment variable overrides."""

    def test_default_thresholds(self) -> None:
        thresholds = get_thresholds()
        assert thresholds.cpu_warning == 75.0
        assert thresholds.cpu_critical == 90.0
        assert thresholds.memory_warning == 80.0
        assert thresholds.memory_critical == 95.0
        assert thresholds.pending_pods_warning == 1
        assert thresholds.pending_pods_critical == 10
        assert thresholds.upgrade_anomaly_minutes == 60

    def test_cpu_critical_override_from_env(self) -> None:
        with patch.dict(os.environ, {"PRESSURE_CPU_CRITICAL": "85"}):
            thresholds = ThresholdConfig()
            assert thresholds.cpu_critical == 85.0

    def test_memory_warning_override_from_env(self) -> None:
        with patch.dict(os.environ, {"PRESSURE_MEMORY_WARNING": "70"}):
            thresholds = ThresholdConfig()
            assert thresholds.memory_warning == 70.0

    def test_upgrade_anomaly_override_from_env(self) -> None:
        with patch.dict(os.environ, {"UPGRADE_ANOMALY_MINUTES": "45"}):
            thresholds = ThresholdConfig()
            assert thresholds.upgrade_anomaly_minutes == 45

    def test_pending_pods_critical_override_from_env(self) -> None:
        with patch.dict(os.environ, {"PRESSURE_PENDING_PODS_CRITICAL": "20"}):
            thresholds = ThresholdConfig()
            assert thresholds.pending_pods_critical == 20

    def test_threshold_config_is_frozen(self) -> None:
        thresholds = get_thresholds()
        with pytest.raises(AttributeError):
            thresholds.cpu_critical = 50.0  # type: ignore[misc]
