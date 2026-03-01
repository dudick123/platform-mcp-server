"""Tests for config.py: cluster mapping, thresholds, environment variable overrides."""

# Note 1: Configuration tests are often undervalued but are among the highest-value
# tests in an infrastructure tool. Misconfigured clusters, wrong subscription IDs,
# or threshold defaults that silently changed can cause production outages. These
# tests act as a compile-time assertion that the configuration data is internally
# consistent and matches documented invariants.

# Note 2: `from __future__ import annotations` is used project-wide to enable
# PEP 563 deferred annotation evaluation. This has no runtime effect for these
# tests, but it is included for consistency so that type checkers and linters
# treat all files uniformly.
from __future__ import annotations

import os

# Note 3: `patch` from `unittest.mock` is the primary tool for injecting test
# doubles at the module or object level. It works as both a context manager
# (`with patch(...)`) and a decorator (`@patch(...)`). Using it as a context manager
# (as done throughout this file) gives fine-grained control over exactly which
# lines of a test run with the patch applied.
from unittest.mock import patch

import pytest

from platform_mcp_server.config import (
    ALL_CLUSTER_IDS,
    CLUSTER_MAP,
    ClusterConfig,
    ThresholdConfig,
    get_thresholds,
    resolve_cluster,
    validate_cluster_config,
)


class TestClusterMap:
    """Tests for the CLUSTER_MAP configuration."""

    def test_cluster_map_has_six_entries(self) -> None:
        # Note 4: Asserting an exact count (6) rather than "at least 1" makes this
        # a structural regression test. If a developer adds a new cluster without
        # updating ALL_CLUSTER_IDS, or removes one without cleaning up the map,
        # this test will fail immediately. Exact-count assertions are often more
        # valuable than lower-bound checks for configuration data.
        assert len(CLUSTER_MAP) == 6

    def test_all_cluster_ids_match_map_keys(self) -> None:
        # Note 5: Comparing set(ALL_CLUSTER_IDS) to set(CLUSTER_MAP.keys()) checks
        # bidirectional consistency — it catches both "cluster in CLUSTER_MAP but
        # missing from ALL_CLUSTER_IDS" and the inverse. Using sets (not lists)
        # means order does not matter and duplicates are ignored for the comparison,
        # which is the correct semantic for checking membership equivalence.
        assert set(ALL_CLUSTER_IDS) == set(CLUSTER_MAP.keys())

    # Note 6: `@pytest.mark.parametrize` generates one distinct test case per tuple
    # in the list. The first argument names the parameters (comma-separated string),
    # and the second is a list of value tuples. Each generated test gets its own
    # node ID in pytest output (e.g., `test_cluster_config_environment_and_region[dev-eastus-dev-eastus]`),
    # making failures easy to pinpoint without shared state between cases.
    @pytest.mark.parametrize(
        "cluster_id,expected_env,expected_region",
        [
            # Note 7: All six cluster combinations (3 environments x 2 regions) are
            # listed exhaustively. For a small, fixed set like this, exhaustive
            # parametrization is preferable to a subset — it guarantees the entire
            # configuration surface is correct, not just a sampled portion.
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
        # Note 8: Three assertions are combined in one test here because they all
        # describe the same unit of truth: a cluster entry in the map has consistent
        # identity fields. If `cluster_id` did not match the map key, every tool
        # that resolves clusters by ID would silently produce wrong results. Grouping
        # related assertions keeps the test readable while still covering the contract.
        config = CLUSTER_MAP[cluster_id]
        assert config.environment == expected_env
        assert config.region == expected_region
        assert config.cluster_id == cluster_id

    def test_each_cluster_has_unique_kubeconfig_context(self) -> None:
        # Note 9: Duplicate kubeconfig contexts would cause kubectl commands to
        # connect to the wrong cluster silently. Converting to a set and comparing
        # the length is the idiomatic Python uniqueness check — if any two contexts
        # are equal, the set will be smaller than the list.
        contexts = [c.kubeconfig_context for c in CLUSTER_MAP.values()]
        assert len(contexts) == len(set(contexts))

    def test_cluster_config_is_frozen(self) -> None:
        # Note 10: Testing immutability (frozen dataclass or frozen Pydantic model)
        # is important for shared global config objects. If `ClusterConfig` were
        # mutable, a test that modifies it could corrupt the state for all subsequent
        # tests in the same process — a classic source of non-deterministic test
        # failures. `# type: ignore[misc]` suppresses the mypy error for the
        # intentional invalid assignment; it documents that we know this is wrong
        # and are testing that Python itself enforces it at runtime.
        config = CLUSTER_MAP["dev-eastus"]
        with pytest.raises(AttributeError):
            config.cluster_id = "other"  # type: ignore[misc]


class TestResolveCluster:
    """Tests for the resolve_cluster function."""

    def test_resolve_valid_cluster(self) -> None:
        # Note 11: `isinstance(config, ClusterConfig)` verifies the return type,
        # not just that the function did not raise. This catches bugs where a
        # function returns a dict instead of a typed model, or returns `None`
        # instead of raising. Pairing the isinstance check with field assertions
        # makes the test comprehensive without being verbose.
        config = resolve_cluster("prod-eastus")
        assert isinstance(config, ClusterConfig)
        assert config.cluster_id == "prod-eastus"
        assert config.environment == "prod"
        assert config.region == "eastus"

    def test_resolve_invalid_cluster_raises_value_error(self) -> None:
        # Note 12: The `match` pattern `"Unknown cluster 'nonexistent'"` checks both
        # the error category ("Unknown cluster") and the specific value echoed back
        # to the user. This matters for operator UX: an error message that repeats
        # the bad input helps operators immediately identify typos rather than
        # searching through logs.
        with pytest.raises(ValueError, match="Unknown cluster 'nonexistent'"):
            resolve_cluster("nonexistent")

    def test_resolve_invalid_cluster_lists_valid_ids(self) -> None:
        # Note 13: `exc_info` is the `ExceptionInfo` object pytest captures inside
        # a `pytest.raises` block. Accessing `exc_info.value` gives the actual
        # exception instance. This pattern is used when you need to inspect the
        # exception beyond what the `match` regex can express — here, iterating over
        # ALL_CLUSTER_IDS to confirm each one appears in the error message.
        with pytest.raises(ValueError, match="dev-eastus") as exc_info:
            resolve_cluster("bad-cluster")
        error_msg = str(exc_info.value)
        # Note 14: Asserting that every valid cluster ID appears in the error message
        # tests the error's helpfulness, not just its correctness. An operator who
        # sees an "Unknown cluster" error with a list of valid options can self-serve
        # the fix. This is a user-experience test masquerading as a functional test.
        for cluster_id in ALL_CLUSTER_IDS:
            assert cluster_id in error_msg


class TestThresholdConfig:
    """Tests for ThresholdConfig defaults and environment variable overrides."""

    def test_default_thresholds(self) -> None:
        # Note 15: Default threshold values are operational parameters — if they
        # change silently, on-call alerts may fire too early, too late, or not at
        # all. Asserting every default in one test makes the contract explicit and
        # creates a diff in code review when defaults are intentionally changed,
        # prompting discussion about the operational impact.
        thresholds = get_thresholds()
        assert thresholds.cpu_warning == 75.0
        assert thresholds.cpu_critical == 90.0
        assert thresholds.memory_warning == 80.0
        assert thresholds.memory_critical == 95.0
        assert thresholds.pending_pods_warning == 1
        assert thresholds.pending_pods_critical == 10
        assert thresholds.upgrade_anomaly_minutes == 60

    def test_cpu_critical_override_from_env(self) -> None:
        # Note 16: `patch.dict(os.environ, {...})` is the canonical way to test
        # environment variable overrides. It temporarily adds/overwrites the specified
        # keys for the duration of the `with` block, then restores the original
        # environment. This is essential for test isolation — without it, setting
        # `os.environ["KEY"] = "value"` would leak into every subsequent test in the
        # process, potentially causing order-dependent failures.
        with patch.dict(os.environ, {"PRESSURE_CPU_CRITICAL": "85"}):
            # Note 17: `ThresholdConfig()` is instantiated *inside* the `with` block
            # so that the environment variable is present when the config class reads
            # it. If instantiation happened outside the block, the env var would not
            # be visible and the test would pass trivially for the wrong reason.
            thresholds = ThresholdConfig()
            assert thresholds.cpu_critical == 85.0

    def test_memory_warning_override_from_env(self) -> None:
        # Note 18: Each override is tested independently rather than setting all env
        # vars at once. Independent tests make failures actionable: if the memory
        # warning override stops working, this test fails while the others pass,
        # pointing directly to the broken field rather than leaving you to bisect
        # a multi-var scenario.
        with patch.dict(os.environ, {"PRESSURE_MEMORY_WARNING": "70"}):
            thresholds = ThresholdConfig()
            assert thresholds.memory_warning == 70.0

    def test_upgrade_anomaly_override_from_env(self) -> None:
        # Note 19: The "45" string is parsed as an integer by the config class.
        # Testing with a non-default value (not 60) proves the override was actually
        # applied, not just that the default happens to match. If this test used "60"
        # it would pass even if the env var override was completely broken.
        with patch.dict(os.environ, {"UPGRADE_ANOMALY_MINUTES": "45"}):
            thresholds = ThresholdConfig()
            assert thresholds.upgrade_anomaly_minutes == 45

    def test_pending_pods_critical_override_from_env(self) -> None:
        with patch.dict(os.environ, {"PRESSURE_PENDING_PODS_CRITICAL": "20"}):
            thresholds = ThresholdConfig()
            assert thresholds.pending_pods_critical == 20

    def test_threshold_config_is_frozen(self) -> None:
        # Note 20: Like ClusterConfig, ThresholdConfig must be immutable. If
        # thresholds were mutable, a tool implementation that accidentally writes to
        # a threshold field would silently affect every subsequent call in the same
        # server process — a hard-to-diagnose memory-corruption-style bug at the
        # Python level. Freezing the dataclass makes this class of bug impossible.
        thresholds = get_thresholds()
        with pytest.raises(AttributeError):
            thresholds.cpu_critical = 50.0  # type: ignore[misc]


class TestValidateClusterConfig:
    """Tests for startup config validation."""

    def test_detects_placeholder_subscription_ids(self) -> None:
        with pytest.raises(RuntimeError, match="placeholder subscription_id detected"):
            validate_cluster_config()

    def test_accepts_real_subscription_ids(self) -> None:
        # Note 22: A UUID-format subscription ID is constructed to simulate a
        # real Azure subscription. The pattern "12345678-1234-1234-1234-123456789abc"
        # follows the UUID v4 format that Azure uses. Using a realistic format
        # (rather than "real_sub_id") tests that the validator's pattern matching
        # correctly recognises valid UUIDs, not just that it accepts any non-placeholder
        # string.
        real_configs = {}
        for cid, cfg in CLUSTER_MAP.items():
            # Note 23: Each ClusterConfig is reconstructed field-by-field, copying all
            # original fields but substituting the subscription_id. This pattern is
            # necessary because ClusterConfig is a frozen dataclass — you cannot do
            # `cfg.subscription_id = "..."`. Instead, you must create a new instance.
            # The `dataclasses.replace()` function is an alternative that is more
            # concise, but the explicit constructor call here makes the intent clear.
            real_configs[cid] = ClusterConfig(
                cluster_id=cfg.cluster_id,
                environment=cfg.environment,
                region=cfg.region,
                subscription_id="12345678-1234-1234-1234-123456789abc",
                resource_group=cfg.resource_group,
                aks_cluster_name=cfg.aks_cluster_name,
                kubeconfig_context=cfg.kubeconfig_context,
            )
        # Note 24: `patch.dict("platform_mcp_server.config.CLUSTER_MAP", real_configs)`
        # replaces the module-level CLUSTER_MAP dictionary for the duration of the
        # `with` block. The string form of the target (`"module.ATTRIBUTE"`) is used
        # because `patch.dict` needs to find and replace the actual dict object in
        # the module's namespace, not a local reference. After the block exits, the
        # original CLUSTER_MAP is automatically restored.
        with patch.dict("platform_mcp_server.config.CLUSTER_MAP", real_configs):
            validate_cluster_config()  # Should not raise
