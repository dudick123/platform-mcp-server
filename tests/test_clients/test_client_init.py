"""Tests for client initialization and lazy API loading."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.clients.k8s_core import K8sCoreClient
from platform_mcp_server.clients.k8s_events import K8sEventsClient
from platform_mcp_server.clients.k8s_metrics import K8sMetricsClient
from platform_mcp_server.clients.k8s_policy import K8sPolicyClient
from platform_mcp_server.config import CLUSTER_MAP


class TestK8sCoreClientInit:
    def test_lazy_api_creation(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sCoreClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sCoreClient(config)
        with patch("platform_mcp_server.clients.k8s_core.load_k8s_api_client") as mock_load:
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        assert api1 is api2
        mock_load.assert_called_once()


class TestK8sEventsClientInit:
    def test_lazy_api_creation(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sEventsClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sEventsClient(config)
        with patch("platform_mcp_server.clients.k8s_events.load_k8s_api_client") as mock_load:
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        assert api1 is api2
        mock_load.assert_called_once()


class TestK8sMetricsClientInit:
    def test_lazy_api_creation(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sMetricsClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sMetricsClient(config)
        with patch("platform_mcp_server.clients.k8s_metrics.load_k8s_api_client") as mock_load:
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        assert api1 is api2
        mock_load.assert_called_once()


class TestK8sPolicyClientInit:
    def test_lazy_api_creation(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sPolicyClient(config)
        assert client._api is None

    def test_get_api_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = K8sPolicyClient(config)
        with patch("platform_mcp_server.clients.k8s_policy.load_k8s_api_client") as mock_load:
            mock_load.return_value = MagicMock()
            api1 = client._get_api()
            api2 = client._get_api()
        assert api1 is api2
        mock_load.assert_called_once()


class TestAzureAksClientInit:
    def test_lazy_credential_creation(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        assert client._credential is None
        assert client._container_client is None
        assert client._monitor_client is None

    def test_get_credential_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        with patch("platform_mcp_server.clients.azure_aks.DefaultAzureCredential") as mock_cred:
            mock_cred.return_value = MagicMock()
            cred1 = client._get_credential()
            cred2 = client._get_credential()
        assert cred1 is cred2
        mock_cred.assert_called_once()

    def test_get_container_client_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        with (
            patch("platform_mcp_server.clients.azure_aks.DefaultAzureCredential"),
            patch("platform_mcp_server.clients.azure_aks.ContainerServiceClient") as mock_cs,
        ):
            mock_cs.return_value = MagicMock()
            c1 = client._get_container_client()
            c2 = client._get_container_client()
        assert c1 is c2
        mock_cs.assert_called_once()

    def test_get_monitor_client_creates_once(self) -> None:
        config = CLUSTER_MAP["prod-eastus"]
        client = AzureAksClient(config)
        with (
            patch("platform_mcp_server.clients.azure_aks.DefaultAzureCredential"),
            patch("platform_mcp_server.clients.azure_aks.MonitorManagementClient") as mock_mon,
        ):
            mock_mon.return_value = MagicMock()
            m1 = client._get_monitor_client()
            m2 = client._get_monitor_client()
        assert m1 is m2
        mock_mon.assert_called_once()
