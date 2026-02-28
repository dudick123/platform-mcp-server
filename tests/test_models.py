"""Tests for models.py: ToolError, all tool input/output models, output scrubbing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_mcp_server.models import (
    NodePoolPressureInput,
    NodePoolPressureOutput,
    NodePoolResult,
    PdbCheckInput,
    PdbCheckOutput,
    PdbRisk,
    PodDetail,
    PodHealthInput,
    PodHealthOutput,
    ToolError,
    UpgradeDurationInput,
    UpgradeDurationOutput,
    UpgradeProgressInput,
    UpgradeProgressOutput,
    UpgradeStatusInput,
    UpgradeStatusOutput,
    scrub_sensitive_values,
)


class TestToolError:
    """Tests for the ToolError model."""

    def test_tool_error_serialization(self) -> None:
        error = ToolError(
            error="Metrics API unavailable",
            source="metrics-server",
            cluster="prod-eastus",
            partial_data=True,
        )
        data = error.model_dump()
        assert data["error"] == "Metrics API unavailable"
        assert data["source"] == "metrics-server"
        assert data["cluster"] == "prod-eastus"
        assert data["partial_data"] is True

    def test_tool_error_partial_data_default_false(self) -> None:
        error = ToolError(
            error="Connection refused",
            source="k8s-api",
            cluster="dev-eastus",
        )
        assert error.partial_data is False

    def test_tool_error_json_roundtrip(self) -> None:
        error = ToolError(
            error="Timeout",
            source="aks-api",
            cluster="staging-westus2",
            partial_data=True,
        )
        json_str = error.model_dump_json()
        restored = ToolError.model_validate_json(json_str)
        assert restored == error


class TestNodePoolPressureModels:
    """Tests for NodePoolPressureInput and NodePoolPressureOutput."""

    def test_input_valid_cluster(self) -> None:
        inp = NodePoolPressureInput(cluster="prod-eastus")
        assert inp.cluster == "prod-eastus"

    def test_input_cluster_all(self) -> None:
        inp = NodePoolPressureInput(cluster="all")
        assert inp.cluster == "all"

    def test_input_invalid_cluster_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NodePoolPressureInput(cluster="invalid-cluster")

    def test_output_with_pools(self) -> None:
        output = NodePoolPressureOutput(
            cluster="prod-eastus",
            pools=[
                NodePoolResult(
                    pool_name="userpool",
                    cpu_requests_percent=85.0,
                    memory_requests_percent=70.0,
                    pending_pods=0,
                    ready_nodes=3,
                    max_nodes=5,
                    pressure_level="warning",
                ),
            ],
            summary="1 of 1 node pools in prod-eastus under pressure",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert len(output.pools) == 1
        assert output.pools[0].pressure_level == "warning"

    def test_output_with_errors(self) -> None:
        output = NodePoolPressureOutput(
            cluster="prod-eastus",
            pools=[],
            summary="No data available",
            timestamp="2026-02-28T12:00:00Z",
            errors=[
                ToolError(
                    error="Metrics unavailable", source="metrics-server", cluster="prod-eastus", partial_data=True
                )
            ],
        )
        assert len(output.errors) == 1


class TestPodHealthModels:
    """Tests for PodHealthInput and PodHealthOutput."""

    def test_input_defaults(self) -> None:
        inp = PodHealthInput(cluster="dev-eastus")
        assert inp.namespace is None
        assert inp.status_filter == "all"
        assert inp.lookback_minutes == 30

    def test_input_with_all_params(self) -> None:
        inp = PodHealthInput(
            cluster="prod-eastus",
            namespace="payments",
            status_filter="pending",
            lookback_minutes=60,
        )
        assert inp.namespace == "payments"
        assert inp.status_filter == "pending"

    def test_input_invalid_status_filter(self) -> None:
        with pytest.raises(ValidationError):
            PodHealthInput(cluster="prod-eastus", status_filter="unknown")

    def test_output_with_pods(self) -> None:
        output = PodHealthOutput(
            cluster="prod-eastus",
            pods=[
                PodDetail(
                    name="test-pod",
                    namespace="default",
                    phase="Pending",
                    reason="Unschedulable",
                    failure_category="scheduling",
                    restart_count=0,
                    last_event="0/12 nodes available: Insufficient cpu",
                ),
            ],
            groups={"scheduling": 1},
            total_matching=1,
            truncated=False,
            summary="1 unhealthy pod in prod-eastus",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert len(output.pods) == 1
        assert output.groups["scheduling"] == 1

    def test_output_truncated(self) -> None:
        output = PodHealthOutput(
            cluster="prod-eastus",
            pods=[],
            groups={},
            total_matching=120,
            truncated=True,
            summary="Showing 50 of 120 matching pods",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert output.truncated is True
        assert output.total_matching == 120


class TestUpgradeStatusModels:
    """Tests for UpgradeStatusInput and UpgradeStatusOutput."""

    def test_input_valid(self) -> None:
        inp = UpgradeStatusInput(cluster="staging-eastus")
        assert inp.cluster == "staging-eastus"

    def test_output_structure(self) -> None:
        output = UpgradeStatusOutput(
            cluster="prod-eastus",
            control_plane_version="1.29.8",
            node_pools=[],
            available_upgrades=["1.30.0"],
            upgrade_active=False,
            summary="prod-eastus running 1.29.8, 1 upgrade available",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert output.upgrade_active is False
        assert "1.30.0" in output.available_upgrades


class TestUpgradeProgressModels:
    """Tests for UpgradeProgressInput and UpgradeProgressOutput."""

    def test_input_with_node_pool(self) -> None:
        inp = UpgradeProgressInput(cluster="prod-eastus", node_pool="userpool")
        assert inp.node_pool == "userpool"

    def test_input_node_pool_optional(self) -> None:
        inp = UpgradeProgressInput(cluster="prod-eastus")
        assert inp.node_pool is None

    def test_output_no_upgrade(self) -> None:
        output = UpgradeProgressOutput(
            cluster="prod-eastus",
            upgrade_in_progress=False,
            nodes=[],
            summary="No upgrade in progress for prod-eastus",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert output.upgrade_in_progress is False


class TestUpgradeDurationModels:
    """Tests for UpgradeDurationInput and UpgradeDurationOutput."""

    def test_input_defaults(self) -> None:
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool")
        assert inp.history_count == 5

    def test_input_custom_history_count(self) -> None:
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=3)
        assert inp.history_count == 3

    def test_output_structure(self) -> None:
        output = UpgradeDurationOutput(
            cluster="prod-eastus",
            node_pool="userpool",
            current_run=None,
            historical=[],
            summary="No active upgrade; no historical data",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert output.current_run is None
        assert output.historical == []


class TestPdbCheckModels:
    """Tests for PdbCheckInput and PdbCheckOutput."""

    def test_input_preflight_default(self) -> None:
        inp = PdbCheckInput(cluster="prod-eastus")
        assert inp.mode == "preflight"
        assert inp.node_pool is None

    def test_input_live_mode(self) -> None:
        inp = PdbCheckInput(cluster="prod-eastus", mode="live")
        assert inp.mode == "live"

    def test_input_invalid_mode(self) -> None:
        with pytest.raises(ValidationError):
            PdbCheckInput(cluster="prod-eastus", mode="invalid")

    def test_output_with_risks(self) -> None:
        output = PdbCheckOutput(
            cluster="prod-eastus",
            mode="preflight",
            risks=[
                PdbRisk(
                    pdb_name="my-pdb",
                    namespace="payments",
                    workload="my-deployment",
                    reason="maxUnavailable=0",
                    affected_pods=3,
                ),
            ],
            summary="1 PDB would block drain in prod-eastus",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert len(output.risks) == 1
        assert output.risks[0].reason == "maxUnavailable=0"


class TestScrubSensitiveValues:
    """Tests for output scrubbing of IPs and subscription IDs."""

    def test_scrub_internal_ip(self) -> None:
        text = "Node 10.240.0.5 is not ready"
        scrubbed = scrub_sensitive_values(text)
        assert "10.240.0.5" not in scrubbed
        assert "[REDACTED_IP]" in scrubbed

    def test_scrub_subscription_id(self) -> None:
        text = "Subscription /subscriptions/12345678-1234-1234-1234-123456789abc/resourceGroups/rg-prod"
        scrubbed = scrub_sensitive_values(text)
        assert "12345678-1234-1234-1234-123456789abc" not in scrubbed

    def test_preserve_node_names(self) -> None:
        text = "Node aks-userpool-00000001 is ready"
        scrubbed = scrub_sensitive_values(text)
        assert "aks-userpool-00000001" in scrubbed

    def test_scrub_resource_group(self) -> None:
        text = "/subscriptions/abc123/resourceGroups/rg-prod-eastus/providers/foo"
        scrubbed = scrub_sensitive_values(text)
        assert "rg-prod-eastus" not in scrubbed

    def test_scrub_empty_string(self) -> None:
        assert scrub_sensitive_values("") == ""

    def test_scrub_no_sensitive_data(self) -> None:
        text = "All 3 pods are healthy"
        assert scrub_sensitive_values(text) == text

    def test_scrub_aks_fqdn(self) -> None:
        text = "Connected to aks-prod.eastus.azmk8s.io"
        scrubbed = scrub_sensitive_values(text)
        assert "azmk8s.io" not in scrubbed
        assert "[REDACTED_FQDN]" in scrubbed

    def test_scrub_vault_hostname(self) -> None:
        text = "Access denied for myvault.vault.azure.net"
        scrubbed = scrub_sensitive_values(text)
        assert "myvault.vault.azure.net" not in scrubbed
        assert "[REDACTED_HOST]" in scrubbed

    def test_scrub_blob_hostname(self) -> None:
        text = "Storage at myaccount.blob.core.windows.net"
        scrubbed = scrub_sensitive_values(text)
        assert "myaccount.blob.core.windows.net" not in scrubbed
        assert "[REDACTED_HOST]" in scrubbed


class TestInputValidationBounds:
    """Tests for input model field constraints."""

    def test_lookback_minutes_default(self) -> None:
        inp = PodHealthInput(cluster="prod-eastus")
        assert inp.lookback_minutes == 30

    def test_lookback_minutes_valid(self) -> None:
        inp = PodHealthInput(cluster="prod-eastus", lookback_minutes=60)
        assert inp.lookback_minutes == 60

    def test_lookback_minutes_too_high(self) -> None:
        with pytest.raises(ValidationError):
            PodHealthInput(cluster="prod-eastus", lookback_minutes=1441)

    def test_lookback_minutes_too_low(self) -> None:
        with pytest.raises(ValidationError):
            PodHealthInput(cluster="prod-eastus", lookback_minutes=0)

    def test_history_count_default(self) -> None:
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool")
        assert inp.history_count == 5

    def test_history_count_valid(self) -> None:
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=50)
        assert inp.history_count == 50

    def test_history_count_too_high(self) -> None:
        with pytest.raises(ValidationError):
            UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=51)

    def test_history_count_too_low(self) -> None:
        with pytest.raises(ValidationError):
            UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=0)
