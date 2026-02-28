"""Shared pod failure classification logic used by pod_health and upgrade_progress."""

from __future__ import annotations

from typing import Any

# Note 1: These constants are defined at module level so they are created once at
# import time and shared across all calls.  Module-level placement also signals to
# readers that these are stable, shared values rather than per-call temporaries.

# Note 2: Each constant uses a plain set literal `{...}` rather than frozenset here,
# but the key property is that set membership tests (`in`) are O(1) average case
# via hashing, compared to O(n) for a list or tuple scan.  The taxonomy groups
# reasons by the layer of the stack that caused the failure:

# Note 3: SCHEDULING_REASONS covers failures that happened before a container even
# started -- the scheduler could not place the pod on any node, usually due to
# resource pressure or taint/toleration mismatches.
# Failure category mapping
SCHEDULING_REASONS = {"Unschedulable", "FailedScheduling", "InsufficientCPU", "InsufficientMemory"}
# Note 4: RUNTIME_REASONS covers failures that occur after the container process
# starts but while it is running: crashes (Error/CrashLoopBackOff), out-of-memory
# kills (OOMKilled), and unknown container state.  OOMKilled is classified here
# because the Linux OOM killer fires at runtime inside the cgroup, not during
# image pull or scheduling.
RUNTIME_REASONS = {"CrashLoopBackOff", "OOMKilled", "Error", "ContainerStatusUnknown"}
# Note 5: REGISTRY_REASONS covers failures where the container runtime could not
# pull the image from a registry.  These are distinct from runtime failures because
# the container process never starts at all; the fix is typically a credentials or
# image name issue, not a code or resource issue.
REGISTRY_REASONS = {"ImagePullBackOff", "ErrImagePull", "ErrImageNeverPull"}
# Note 6: CONFIG_REASONS covers failures where the image was pulled but the
# container could not be created or started due to bad configuration -- invalid
# environment variables, missing secrets, or a bad entrypoint.
CONFIG_REASONS = {"CreateContainerConfigError", "InvalidImageName", "RunContainerError"}


def categorize_failure(reason: str | None, container_statuses: list[dict[str, Any]]) -> str:
    """Determine failure category from reason and container state."""
    # Note 7: The pod-level `reason` field is checked first.  Scheduling failures
    # are surfaced at the pod level rather than the container level because no
    # container was ever started, so there is no per-container state to inspect.
    if reason and reason in SCHEDULING_REASONS:
        return "scheduling"

    # Note 8: The fallback chain walks each container status and inspects
    # `state.waiting.reason` before falling back to `last_terminated.reason`.
    # `state.waiting` reflects the *current* blocking condition (e.g. the image
    # is still being pulled or the container is in back-off), whereas
    # `last_terminated` records why the *previous* container instance exited.
    # Checking `waiting` first gives the most actionable current diagnosis.
    for cs in container_statuses:
        waiting = cs.get("state", {}).get("waiting", {})
        waiting_reason = waiting.get("reason", "")
        if waiting_reason in SCHEDULING_REASONS:
            return "scheduling"
        if waiting_reason in RUNTIME_REASONS:
            return "runtime"
        if waiting_reason in REGISTRY_REASONS:
            return "registry"
        if waiting_reason in CONFIG_REASONS:
            return "config"

        # Note 9: `last_terminated` is a separate sub-object that records the
        # exit code and reason from the most recently completed container run.
        # It is only populated after at least one execution attempt, so it must
        # be checked independently of the current `waiting` state.
        last_term = cs.get("last_terminated", {})
        if last_term.get("reason") == "OOMKilled":
            # Note 10: OOMKilled from last_terminated is always "runtime" because
            # the kernel's OOM killer terminates a running process, not a pending
            # or configuring one.  The container ran but consumed too much memory.
            return "runtime"

    # Note 11: If no container-level reason was matched, fall back to the pod-level
    # reason field for the remaining non-scheduling categories.  This handles cases
    # where the Kubernetes API surfaces the reason at the pod level only.
    if reason and reason in RUNTIME_REASONS:
        return "runtime"
    if reason and reason in REGISTRY_REASONS:
        return "registry"
    if reason and reason in CONFIG_REASONS:
        return "config"

    return "unknown"


def is_unhealthy(pod: dict[str, Any]) -> bool:
    """Check if a pod is currently in an unhealthy state."""
    phase = pod.get("phase", "")
    if phase in ("Pending", "Failed", "Unknown"):
        return True

    for cs in pod.get("container_statuses", []):
        waiting = cs.get("state", {}).get("waiting", {})
        # Note 12: The `|` operator on sets produces a new union set at call time.
        # Because these three sets are module-level constants this expression is
        # cheap, but if performance were critical you could pre-compute a combined
        # UNHEALTHY_REASONS constant to avoid the union on every iteration.
        if waiting.get("reason") in RUNTIME_REASONS | REGISTRY_REASONS | CONFIG_REASONS:
            return True
        # Note 13: The OOMKilled check on last_terminated mirrors the logic in
        # categorize_failure: a pod whose most recent container run was OOM-killed
        # is considered unhealthy even if it is currently in a brief "waiting"
        # state between restart attempts.
        if cs.get("last_terminated", {}).get("reason") == "OOMKilled":
            return True

    return False
