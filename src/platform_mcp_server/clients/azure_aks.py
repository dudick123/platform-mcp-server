"""AKS REST API wrapper — cluster versions, upgrade profiles, activity log."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

# Note 1: DefaultAzureCredential implements a credential chain: it tries, in order,
# Note 2: environment variables (AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID),
# Note 3: then workload identity, then managed identity, then the Azure CLI token cache,
# Note 4: then Visual Studio Code, then an interactive browser login. This means the same
# Note 5: code works in CI (env vars), on Azure VMs (managed identity), and on developer
# Note 6: laptops (az CLI) without any conditional logic in the application itself.
from azure.identity import DefaultAzureCredential

# Note 7: ContainerServiceClient talks to the Microsoft.ContainerService management plane.
# Note 8: It is used for cluster CRUD operations, node pool management, and upgrade profiles.
from azure.mgmt.containerservice import ContainerServiceClient

# Note 9: MonitorManagementClient talks to the Azure Monitor management plane. It provides
# Note 10: access to Activity Logs (audit trail of ARM operations) which is a separate API
# Note 11: surface from the container service plane -- hence a second client is required.
from azure.mgmt.monitor import MonitorManagementClient

from platform_mcp_server.config import ClusterConfig

log = structlog.get_logger()


class AzureAksClient:
    """Wrapper around Azure AKS management APIs."""

    def __init__(self, cluster_config: ClusterConfig) -> None:
        self._config = cluster_config
        # Note 12: The three private attributes are initialised to None here rather than to
        # Note 13: real SDK objects. This is the lazy-initialisation (or "lazy singleton")
        # Note 14: pattern: the actual Azure SDK clients are not created until the first method
        # Note 15: call that needs them. This avoids authenticating and opening network
        # Note 16: connections at import time, which would slow startup and fail in environments
        # Note 17: where Azure credentials are not yet available (e.g. during unit tests).
        self._container_client: ContainerServiceClient | None = None
        self._monitor_client: MonitorManagementClient | None = None
        self._credential: DefaultAzureCredential | None = None
        # RLock is needed because _get_container_client and _get_monitor_client
        # internally call _get_credential — a non-reentrant Lock would deadlock.
        self._lock = threading.RLock()

    def _get_credential(self) -> DefaultAzureCredential:
        # Note 18: The "if None, create and cache" pattern ensures that DefaultAzureCredential
        # Note 19: is instantiated exactly once per AzureAksClient instance. Reusing the same
        # Note 20: credential object allows the SDK to cache and refresh tokens internally.
        with self._lock:
            if self._credential is None:
                self._credential = DefaultAzureCredential()
            return self._credential

    def _get_container_client(self) -> ContainerServiceClient:
        with self._lock:
            if self._container_client is None:
                self._container_client = ContainerServiceClient(
                    credential=self._get_credential(),
                    subscription_id=self._config.subscription_id,
                )
            return self._container_client

    def _get_monitor_client(self) -> MonitorManagementClient:
        with self._lock:
            if self._monitor_client is None:
                self._monitor_client = MonitorManagementClient(
                    credential=self._get_credential(),
                    subscription_id=self._config.subscription_id,
                )
            return self._monitor_client

    async def get_cluster_info(self) -> dict[str, Any]:
        """Get cluster version and basic info from AKS API.

        Returns dict with control_plane_version, provisioning_state, and node_pools.
        """
        client = self._get_container_client()
        try:
            # Note 21: client.managed_clusters.get() maps directly to the Azure Resource Manager
            # Note 22: REST call GET /subscriptions/{sub}/resourceGroups/{rg}/providers/
            # Note 23: Microsoft.ContainerService/managedClusters/{name}. The Python SDK
            # Note 24: deserialises the JSON response into a ManagedCluster model object.
            cluster = await asyncio.to_thread(
                client.managed_clusters.get,
                self._config.resource_group,
                self._config.aks_cluster_name,
            )
        except Exception:
            log.error(
                "failed_to_get_cluster_info",
                cluster=self._config.cluster_id,
            )
            raise

        node_pools = []
        # Note 25: agent_pool_profiles is the list of node pool configurations embedded in the
        # Note 26: cluster object. The "or []" guard handles the case where the field is None,
        # Note 27: which can happen on partially-provisioned clusters, avoiding a TypeError.
        for pool in cluster.agent_pool_profiles or []:
            node_pools.append(
                {
                    "name": pool.name,
                    "vm_size": pool.vm_size,
                    "count": pool.count,
                    "min_count": pool.min_count,
                    "max_count": pool.max_count,
                    # Note 28: current_orchestrator_version holds the version the node pool is
                    # Note 29: actually running right now. orchestrator_version holds the desired
                    # Note 30: (target) version. During an in-flight upgrade the two differ;
                    # Note 31: after upgrade completes they converge. The "or" fallback handles
                    # Note 32: clusters where only orchestrator_version is populated (older API).
                    "current_version": pool.current_orchestrator_version or pool.orchestrator_version,
                    "target_version": pool.orchestrator_version,
                    "provisioning_state": pool.provisioning_state,
                    # Note 33: power_state.code is either "Running" or "Stopped". AKS supports
                    # Note 34: stopping node pools to save compute costs outside business hours.
                    # Note 35: The conditional guards against power_state being None on clusters
                    # Note 36: that predate the power state feature in the AKS API.
                    "power_state": pool.power_state.code if pool.power_state else None,
                    "os_type": pool.os_type,
                    "mode": pool.mode,
                }
            )

        return {
            "control_plane_version": cluster.kubernetes_version,
            "provisioning_state": cluster.provisioning_state,
            "node_pools": node_pools,
            "fqdn": cluster.fqdn,
        }

    async def get_node_pool_state(self, pool_name: str) -> dict[str, Any]:
        """Get the state of a specific node pool.

        Args:
            pool_name: The name of the node pool.

        Returns dict with node pool details including provisioning state.
        """
        client = self._get_container_client()
        try:
            pool = await asyncio.to_thread(
                client.agent_pools.get,
                self._config.resource_group,
                self._config.aks_cluster_name,
                pool_name,
            )
        except Exception:
            log.error(
                "failed_to_get_node_pool",
                cluster=self._config.cluster_id,
                pool=pool_name,
            )
            raise

        return {
            "name": pool.name,
            "count": pool.count,
            "min_count": pool.min_count,
            "max_count": pool.max_count,
            "current_version": pool.current_orchestrator_version or pool.orchestrator_version,
            "target_version": pool.orchestrator_version,
            "provisioning_state": pool.provisioning_state,
            "power_state": pool.power_state.code if pool.power_state else None,
        }

    async def get_upgrade_profile(self) -> dict[str, Any]:
        """Get available upgrade versions for the cluster.

        Returns dict with control plane and per-pool available upgrades.
        """
        client = self._get_container_client()
        try:
            # Note 37: get_upgrade_profile() is a dedicated ARM endpoint separate from the main
            # Note 38: cluster GET. Azure computes available upgrades dynamically -- they depend
            # Note 39: on the current version, regional rollout status, and Microsoft's support
            # Note 40: policy -- so caching them inside the cluster object would go stale quickly.
            # Note 41: Making a separate call ensures the LLM always sees current upgrade options.
            profile = await asyncio.to_thread(
                client.managed_clusters.get_upgrade_profile,
                self._config.resource_group,
                self._config.aks_cluster_name,
            )
        except Exception:
            log.error(
                "failed_to_get_upgrade_profile",
                cluster=self._config.cluster_id,
            )
            raise

        control_plane_upgrades = []
        if profile.control_plane_profile and profile.control_plane_profile.upgrades:
            control_plane_upgrades = [u.kubernetes_version for u in profile.control_plane_profile.upgrades if u]

        pool_upgrades: dict[str, list[str]] = {}
        for pool_profile in profile.agent_pool_profiles or []:
            versions: list[str] = []
            if pool_profile.upgrades:
                versions = [str(u.kubernetes_version) for u in pool_profile.upgrades if u]
            if pool_profile.name:
                pool_upgrades[str(pool_profile.name)] = versions

        return {
            "control_plane_version": (
                profile.control_plane_profile.kubernetes_version if profile.control_plane_profile else None
            ),
            "control_plane_upgrades": control_plane_upgrades,
            "pool_upgrades": pool_upgrades,
        }

    async def get_activity_log_upgrades(
        self,
        count: int = 5,
    ) -> list[dict[str, Any]]:
        """Get historical upgrade records from Azure Activity Log.

        Queries the last 90 days of activity log for AKS upgrade operations.

        Args:
            count: Maximum number of historical records to return.

        Returns a list of upgrade records with date, duration, and version info.
        """
        count = min(count, 50)
        client = self._get_monitor_client()
        resource_id = (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.ContainerService"
            f"/managedClusters/{self._config.aks_cluster_name}"
        )
        now = datetime.now(tz=UTC)
        ninety_days_ago = now - timedelta(days=90)
        # Note 42: The filter string uses OData query syntax, which is the query language for
        # Note 43: Azure Resource Manager list operations. eventTimestamp fields must be in
        # Note 44: ISO 8601 format (e.g. "2025-01-01T00:00:00+00:00"). The operationName filter
        # Note 45: restricts results to cluster write operations, which is the ARM operation
        # Note 46: emitted when AKS starts or completes an upgrade. Without this filter the
        # Note 47: 90-day window could return thousands of unrelated log entries.
        filter_str = (
            f"eventTimestamp ge '{ninety_days_ago.isoformat()}' "
            f"and eventTimestamp le '{now.isoformat()}' "
            f"and resourceUri eq '{resource_id}' "
            f"and operationName.value eq 'Microsoft.ContainerService/managedClusters/write'"
        )

        try:
            records = await asyncio.to_thread(
                self._fetch_activity_logs, client, filter_str, count
            )
        except Exception:
            log.error(
                "failed_to_get_activity_log",
                cluster=self._config.cluster_id,
            )
            raise

        return records

    def _fetch_activity_logs(
        self,
        client: MonitorManagementClient,
        filter_str: str,
        count: int,
    ) -> list[dict[str, Any]]:
        """Synchronous helper that fetches and iterates the activity log paginator."""
        logs = client.activity_logs.list(filter=filter_str)

        records: list[dict[str, Any]] = []
        for entry in logs:
            # Note 48: The Activity Log API returns a lazy iterator backed by paginated HTTP
            # Note 49: calls. Checking len(records) >= count before processing each entry and
            # Note 50: breaking early stops further page fetches once enough records have been
            # Note 51: collected, avoiding unnecessary network round-trips for data that will
            # Note 52: not be used. The Azure SDK does not support server-side $top on this API.
            if len(records) >= count:
                break
            if entry.status and entry.status.value == "Succeeded":
                duration_seconds = None
                if entry.event_timestamp and entry.submission_timestamp:
                    # Note 53: submission_timestamp is when ARM accepted and began processing the
                    # Note 54: operation (i.e. when the upgrade started). event_timestamp is when
                    # Note 55: the operation reached its terminal state (succeeded or failed).
                    # Note 56: Subtracting the two gives the wall-clock duration of the upgrade.
                    delta = entry.event_timestamp - entry.submission_timestamp
                    duration_seconds = delta.total_seconds()

                records.append(
                    {
                        "date": entry.event_timestamp.isoformat() if entry.event_timestamp else None,
                        "operation": entry.operation_name.value if entry.operation_name else None,
                        "status": entry.status.value,
                        "duration_seconds": duration_seconds,
                        "description": entry.description,
                    }
                )
        return records
