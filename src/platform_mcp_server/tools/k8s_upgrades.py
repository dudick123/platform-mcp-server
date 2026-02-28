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

    # Note 1: `cluster_info` and `upgrade_profile` are fetched in separate try/except
    # Note 2: blocks rather than a single combined call because they are independent
    # Note 3: failure domains: the cluster info endpoint can succeed even when the
    # Note 4: upgrade profile endpoint is unavailable (e.g., feature not enabled on
    # Note 5: the subscription). Separating them lets us return partial data with
    # Note 6: structured errors instead of failing the entire request on one timeout.
    # Get cluster info
    cluster_info: dict[str, Any] | None = None
    try:
        cluster_info = await aks_client.get_cluster_info()
    except Exception:
        errors.append(
            ToolError(
                error="Failed to get cluster info",
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

    # Note 7: `cluster_info is None` is the early-exit guard: without the cluster's
    # Note 8: basic metadata (version, pool list) there is nothing meaningful to build
    # Note 9: a status from. We return a structured UpgradeStatusOutput rather than
    # Note 10: raising so the fan-out caller can collect results across all clusters
    # Note 11: without a single failure aborting the entire batch.
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
    # Note 12: `upgrade_active` is initialised to False at the loop level, not inside
    # Note 13: the loop, because it is an aggregate flag: if *any* pool is upgrading the
    # Note 14: whole cluster is considered mid-upgrade. Setting it inside the loop would
    # Note 15: require an extra reduction step; hoisting it here keeps the logic flat.
    upgrade_active = False
    for pool in cluster_info.get("node_pools", []):
        current_ver = pool.get("current_version")
        target_ver = pool.get("target_version")
        # Note 16: The upgrade detection uses two signals joined by OR because AKS may
        # Note 17: report `provisioning_state == "Upgrading"` before the target version
        # Note 18: field is populated, and may leave provisioning_state as "Succeeded"
        # Note 19: while current_ver != target_ver during a slow roll. Either signal alone
        # Note 20: can miss a window; both together give full coverage.
        is_upgrading = pool.get("provisioning_state") == "Upgrading" or (
            current_ver is not None and target_ver is not None and current_ver != target_ver
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
    # Note 21: `asyncio.gather(*tasks, return_exceptions=True)` runs all coroutines
    # Note 22: concurrently on the event loop. The critical detail is `return_exceptions=True`:
    # Note 23: without it, the first raised exception would immediately cancel the remaining
    # Note 24: tasks and propagate out of gather, discarding results already computed.
    # Note 25: With it, exceptions are captured as regular return values in the results
    # Note 26: list, letting the loop below inspect each cluster outcome independently.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs: list[UpgradeStatusOutput] = []
    for cid, result in zip(ALL_CLUSTER_IDS, results, strict=True):
        # Note 27: `isinstance(result, BaseException)` is deliberately broad: it catches
        # Note 28: subclasses of both `Exception` (runtime errors) and `SystemExit`/
        # Note 29: `KeyboardInterrupt` (which inherit from BaseException, not Exception).
        # Note 30: Using BaseException here means no failure mode can silently slip through
        # Note 31: and be mistakenly appended to `outputs` as a valid UpgradeStatusOutput.
        if isinstance(result, BaseException):
            log.error("fan_out_cluster_failed", tool="get_kubernetes_upgrade_status", cluster=cid, error=str(result))
        else:
            outputs.append(result)
    return outputs
