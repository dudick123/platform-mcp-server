"""Client wrappers for Kubernetes and Azure APIs."""

# Note 1: `from __future__ import annotations` defers annotation evaluation so that
# return type hints referencing k8s_client types are resolved lazily, avoiding
# circular-import issues at module load time.
from __future__ import annotations

from kubernetes import client as k8s_client

# Note 2: `new_client_from_config` constructs a fully isolated ApiClient bound to a
# single kubeconfig context without touching any global SDK state.  The alternative,
# `load_incluster_config`, mutates a process-wide singleton and would race when
# multiple coroutines load different contexts concurrently.
from kubernetes.config import new_client_from_config


# Note 3: Defining this as a package-level factory function (rather than a module
# global client) means each caller receives its own ApiClient instance.  This is
# essential for concurrent fan-out across multiple clusters: each task can send
# requests to a different cluster context simultaneously without one task's auth
# headers or TLS settings bleeding into another's.
def load_k8s_api_client(context: str) -> k8s_client.ApiClient:
    """Create an isolated Kubernetes API client for the given kubeconfig context.

    Uses new_client_from_config to avoid mutating the global K8s SDK configuration,
    which is critical for safe concurrent fan-out across multiple clusters.
    """
    # Note 4: Passing `context` by keyword ensures the correct kubeconfig context
    # is selected even if the library's positional argument order changes in a
    # future version, and makes the call site self-documenting.
    return new_client_from_config(context=context)
