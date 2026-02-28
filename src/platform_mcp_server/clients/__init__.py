"""Client wrappers for Kubernetes and Azure APIs."""

from __future__ import annotations

from kubernetes import client as k8s_client
from kubernetes.config import new_client_from_config


def load_k8s_api_client(context: str) -> k8s_client.ApiClient:
    """Create an isolated Kubernetes API client for the given kubeconfig context.

    Uses new_client_from_config to avoid mutating the global K8s SDK configuration,
    which is critical for safe concurrent fan-out across multiple clusters.
    """
    return new_client_from_config(context=context)
