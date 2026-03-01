"""Tests for config.py: cluster mapping, thresholds, environment variable overrides."""

from __future__ import annotations

import os
import textwrap
from unittest.mock import patch

import pytest

from platform_mcp_server.config import (
    ALL_CLUSTER_IDS,
    CLUSTER_MAP,
    ClusterConfig,
    ThresholdConfig,
    _load_cluster_map,
    get_thresholds,
    load_cluster_map,
    resolve_cluster,
    validate_cluster_config,
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


class TestValidateClusterConfig:
    """Tests for startup config validation."""

    def test_detects_placeholder_subscription_ids(self) -> None:
        with pytest.raises(RuntimeError, match="placeholder subscription_id detected"):
            validate_cluster_config()

    def test_accepts_real_subscription_ids(self) -> None:
        real_configs = {}
        for cid, cfg in CLUSTER_MAP.items():
            real_configs[cid] = ClusterConfig(
                cluster_id=cfg.cluster_id,
                environment=cfg.environment,
                region=cfg.region,
                subscription_id="12345678-1234-1234-1234-123456789abc",
                resource_group=cfg.resource_group,
                aks_cluster_name=cfg.aks_cluster_name,
                kubeconfig_context=cfg.kubeconfig_context,
            )
        with patch.dict("platform_mcp_server.config.CLUSTER_MAP", real_configs):
            validate_cluster_config()  # Should not raise


@pytest.fixture(autouse=False)
def _restore_cluster_map() -> object:
    """Save and restore CLUSTER_MAP and ALL_CLUSTER_IDS after tests that call load_cluster_map."""
    saved_map = dict(CLUSTER_MAP)
    saved_ids = list(ALL_CLUSTER_IDS)
    yield
    CLUSTER_MAP.clear()
    CLUSTER_MAP.update(saved_map)
    ALL_CLUSTER_IDS.clear()
    ALL_CLUSTER_IDS.extend(saved_ids)


class TestLoadClusterMap:
    """Tests for YAML-based cluster loading."""

    def test_load_cluster_map_valid_yaml(self, tmp_path: object) -> None:
        from pathlib import Path

        p = Path(str(tmp_path)) / "clusters.yaml"
        p.write_text(
            textwrap.dedent("""\
            clusters:
              test-eastus:
                environment: test
                region: eastus
                subscription_id: "00000000-0000-0000-0000-000000000000"
                resource_group: rg-test-eastus
                aks_cluster_name: aks-test-eastus
                kubeconfig_context: aks-test-eastus
        """)
        )
        result = _load_cluster_map(p)
        assert "test-eastus" in result
        cfg = result["test-eastus"]
        assert isinstance(cfg, ClusterConfig)
        assert cfg.cluster_id == "test-eastus"
        assert cfg.environment == "test"
        assert cfg.region == "eastus"
        assert cfg.subscription_id == "00000000-0000-0000-0000-000000000000"

    def test_load_cluster_map_file_not_found(self, tmp_path: object) -> None:
        from pathlib import Path

        p = Path(str(tmp_path)) / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError, match="Cluster configuration file not found"):
            _load_cluster_map(p)

    def test_load_cluster_map_missing_required_field(self, tmp_path: object) -> None:
        from pathlib import Path

        p = Path(str(tmp_path)) / "clusters.yaml"
        p.write_text(
            textwrap.dedent("""\
            clusters:
              broken-cluster:
                environment: dev
                region: eastus
        """)
        )
        with pytest.raises(ValueError, match="missing required fields"):
            _load_cluster_map(p)

    def test_load_cluster_map_empty_clusters(self, tmp_path: object) -> None:
        from pathlib import Path

        p = Path(str(tmp_path)) / "clusters.yaml"
        p.write_text("clusters: {}\n")
        with pytest.raises(ValueError, match="empty or invalid"):
            _load_cluster_map(p)

    @pytest.mark.usefixtures("_restore_cluster_map")
    def test_load_cluster_map_env_var_override(self, tmp_path: object) -> None:
        from pathlib import Path

        p = Path(str(tmp_path)) / "custom.yaml"
        p.write_text(
            textwrap.dedent("""\
            clusters:
              custom-cluster:
                environment: custom
                region: westus
                subscription_id: "11111111-1111-1111-1111-111111111111"
                resource_group: rg-custom
                aks_cluster_name: aks-custom
                kubeconfig_context: aks-custom
        """)
        )
        with patch.dict(os.environ, {"PLATFORM_MCP_CLUSTERS": str(p)}):
            result = load_cluster_map()
        assert "custom-cluster" in result

    @pytest.mark.usefixtures("_restore_cluster_map")
    def test_load_cluster_map_populates_all_cluster_ids(self, tmp_path: object) -> None:
        from pathlib import Path

        p = Path(str(tmp_path)) / "clusters.yaml"
        p.write_text(
            textwrap.dedent("""\
            clusters:
              a-cluster:
                environment: dev
                region: eastus
                subscription_id: "22222222-2222-2222-2222-222222222222"
                resource_group: rg-a
                aks_cluster_name: aks-a
                kubeconfig_context: aks-a
              b-cluster:
                environment: staging
                region: westus
                subscription_id: "33333333-3333-3333-3333-333333333333"
                resource_group: rg-b
                aks_cluster_name: aks-b
                kubeconfig_context: aks-b
        """)
        )
        with patch.dict(os.environ, {"PLATFORM_MCP_CLUSTERS": str(p)}):
            load_cluster_map()

        from platform_mcp_server.config import ALL_CLUSTER_IDS as ids

        assert "a-cluster" in ids
        assert "b-cluster" in ids
        assert len(ids) == 2

    def test_load_cluster_map_missing_clusters_key(self, tmp_path: object) -> None:
        from pathlib import Path

        p = Path(str(tmp_path)) / "clusters.yaml"
        p.write_text("other_key: value\n")
        with pytest.raises(ValueError, match="top-level 'clusters' key"):
            _load_cluster_map(p)
