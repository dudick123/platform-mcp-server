"""get_kubernetes_upgrade_status — control plane and node pool version state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.config import ALL_CLUSTER_IDS, resolve_cluster
from platform_mcp_server.models import NodePoolVersionInfo, ToolError, UpgradeStatusOutput

log = structlog.get_logger()


async def get_upgrade_status_handler(cluster_id: str) -> UpgradeStatusOutput:
    """Core handler for get_kubernetes_upgrade_status on a single cluster."""
    config = resolve_cluster(cluster_id)
    aks_client = AzureAksClient(config)
    errors: list[ToolError] = []

    # Get cluster info
    cluster_info: dict[str, Any] | None = None
    try:
        cluster_info = await aks_client.get_cluster_info()
    except Exception as e:
        errors.append(
            ToolError(
                error=f"Failed to get cluster info: {type(e).__name__}",
                source="aks-api",
                cluster=cluster_id,
                partial_data=True,
            )
        )

    # Get upgrade profile
    upgrade_profile: dict[str, Any] | None = None
    try:
        upgrade_profile = await aks_client.get_upgrade_profile()
    except Exception:
        errors.append(
            ToolError(
                error="Failed to get upgrade profile",
                source="aks-api",
                cluster=cluster_id,
                partial_data=True,
            )
        )

    if cluster_info is None:
        return UpgradeStatusOutput(
            cluster=cluster_id,
            control_plane_version="unknown",
            node_pools=[],
            available_upgrades=[],
            upgrade_active=False,
            summary=f"Failed to retrieve data for {cluster_id}",
            timestamp=datetime.now(tz=UTC).isoformat(),
            errors=errors,
        )

    # Build node pool version info
    node_pools: list[NodePoolVersionInfo] = []
    upgrade_active = False
    for pool in cluster_info.get("node_pools", []):
        is_upgrading = pool.get("provisioning_state") == "Upgrading" or (
            pool.get("current_version") != pool.get("target_version")
        )
        if is_upgrading:
            upgrade_active = True

        node_pools.append(
            NodePoolVersionInfo(
                pool_name=pool["name"],
                current_version=pool.get("current_version", "unknown"),
                target_version=pool.get("target_version") if is_upgrading else None,
                upgrading=is_upgrading,
            )
        )

    # Available upgrades from profile
    available_upgrades: list[str] = []
    if upgrade_profile:
        available_upgrades = upgrade_profile.get("control_plane_upgrades", [])

    cp_version = cluster_info.get("control_plane_version", "unknown")
    upgrade_count = len(available_upgrades)
    summary = f"{cluster_id} running {cp_version}"
    if upgrade_active:
        summary += ", upgrade in progress"
    elif upgrade_count > 0:
        summary += f", {upgrade_count} upgrade{'s' if upgrade_count != 1 else ''} available"

    return UpgradeStatusOutput(
        cluster=cluster_id,
        control_plane_version=cp_version,
        node_pools=node_pools,
        available_upgrades=available_upgrades,
        upgrade_active=upgrade_active,
        summary=summary,
        timestamp=datetime.now(tz=UTC).isoformat(),
        errors=errors,
    )


async def get_upgrade_status_all() -> list[UpgradeStatusOutput]:
    """Fan-out get_kubernetes_upgrade_status to all clusters concurrently."""
    tasks = [get_upgrade_status_handler(cid) for cid in ALL_CLUSTER_IDS]
    return list(await asyncio.gather(*tasks, return_exceptions=False))
