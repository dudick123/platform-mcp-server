"""Tests for models.py: ToolError, all tool input/output models, output scrubbing."""

# Note 1: Model tests validate the data contract between tool implementations and
# their callers (typically an LLM or a human operator via MCP). Pydantic models
# enforce schemas at runtime, so these tests verify that the schema is both
# correctly defined (rejects invalid data) and correctly defaulted (optional fields
# have sensible defaults). They also document valid and invalid inputs.

# Note 2: `from __future__ import annotations` is a project-wide convention that
# makes all type annotations lazy strings. This avoids forward-reference errors
# and is required when models reference each other in complex hierarchies.
from __future__ import annotations

import pytest

# Note 3: `ValidationError` is Pydantic's exception type for schema violations.
# It is imported here so tests can use it with `pytest.raises(ValidationError)`
# to assert that Pydantic rejected a bad input. It carries structured field-level
# error information, which Pydantic uses to produce detailed error messages.
from pydantic import ValidationError

from platform_mcp_server.models import (
    AffectedPod,
    NodePoolPressureInput,
    NodePoolPressureOutput,
    NodePoolResult,
    PdbCheckInput,
    PdbCheckOutput,
    PdbRisk,
    PodDetail,
    PodHealthInput,
    PodHealthOutput,
    PodTransitionSummary,
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
        # Note 4: `model_dump()` is Pydantic v2's method for converting a model
        # instance to a plain Python dict. Testing serialization verifies that field
        # names in the dict match what downstream consumers (e.g., JSON APIs, logging
        # systems) will receive. A model with a field named `error_message` that
        # serializes to `"errorMessage"` would silently break any consumer expecting
        # snake_case keys.
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
        # Note 5: Asserting `is True` (not just truthiness) verifies the boolean
        # type is preserved through serialization. `assert data["partial_data"]`
        # would pass even if the field contained the integer `1` or the string
        # `"true"`, which might cause downstream type errors in strongly typed
        # consumers.
        assert data["partial_data"] is True

    def test_tool_error_partial_data_default_false(self) -> None:
        # Note 6: Testing defaults independently from the full constructor is a
        # defensive practice. If the default is later changed from `False` to `None`
        # or removed entirely, this test fails and forces the developer to consider
        # whether all consumers of `partial_data` handle the new default correctly.
        error = ToolError(
            error="Connection refused",
            source="k8s-api",
            cluster="dev-eastus",
        )
        assert error.partial_data is False

    def test_tool_error_json_roundtrip(self) -> None:
        # Note 7: A JSON roundtrip test (`model -> JSON string -> model`) is the
        # gold standard for verifying serialization completeness. It catches bugs
        # where a field serializes correctly to a dict but loses type information
        # (e.g., an Enum becomes a string) during JSON encoding, causing
        # `model_validate_json` to reconstruct a subtly different object.
        # The `assert restored == error` comparison uses Pydantic's model equality,
        # which compares all field values structurally.
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
        # Note 8: Constructing the input model and then asserting the field value
        # verifies that Pydantic did not silently transform the input (e.g., strip
        # whitespace, lowercase, or alias the field). This is especially important
        # for cluster IDs used as dictionary keys elsewhere in the system.
        inp = NodePoolPressureInput(cluster="prod-eastus")
        assert inp.cluster == "prod-eastus"

    def test_input_cluster_all(self) -> None:
        # Note 9: The special value "all" means "aggregate across every cluster".
        # It must be explicitly allowed by the cluster field's validator, which
        # otherwise would only accept values from the known cluster ID list.
        # Testing "all" separately from a specific cluster ID ensures the special
        # case is not accidentally removed when the validator logic is refactored.
        inp = NodePoolPressureInput(cluster="all")
        assert inp.cluster == "all"

    def test_input_invalid_cluster_rejected(self) -> None:
        # Note 10: `pytest.raises(ValidationError)` without a `match` argument
        # asserts only that Pydantic raised a validation error — not which field
        # failed or what the message says. This is appropriate when the exact error
        # message is an implementation detail, but the field-level failure (cluster
        # field rejects unknown values) is part of the public contract.
        with pytest.raises(ValidationError):
            NodePoolPressureInput(cluster="invalid-cluster")

    def test_output_with_pools(self) -> None:
        # Note 11: Output model tests verify that complex nested structures (a list
        # of `NodePoolResult` objects inside `NodePoolPressureOutput`) round-trip
        # correctly through Pydantic's validation. If a nested model's field
        # constraints are wrong (e.g., `pressure_level` only accepts "low" but the
        # code produces "warning"), this test catches it.
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
        # Note 12: The `errors` field on output models supports the partial-data
        # pattern: a tool can return whatever data it collected alongside structured
        # error objects describing what it could not collect. This test verifies the
        # errors list is stored correctly and accessible. The empty `pools` list
        # combined with a non-empty `errors` list represents the "total failure"
        # scenario where no useful data was returned.
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
        # Note 13: Testing that optional fields have correct defaults is just as
        # important as testing required fields. Default values define the baseline
        # behavior experienced by operators who omit optional parameters. A changed
        # default (e.g., `lookback_minutes` shifting from 30 to 60) affects every
        # caller that relies on the default and must be a conscious decision.
        inp = PodHealthInput(cluster="dev-eastus")
        assert inp.namespace is None
        assert inp.status_filter == "all"
        assert inp.lookback_minutes == 30

    def test_input_with_all_params(self) -> None:
        # Note 14: This test exercises the "fully specified" input path where all
        # optional parameters are provided. It verifies Pydantic stores the provided
        # values rather than falling back to defaults, which would happen if a field
        # were accidentally marked as read-only or its setter were broken.
        inp = PodHealthInput(
            cluster="prod-eastus",
            namespace="payments",
            status_filter="pending",
            lookback_minutes=60,
        )
        assert inp.namespace == "payments"
        assert inp.status_filter == "pending"

    def test_input_invalid_status_filter(self) -> None:
        # Note 15: `status_filter` is likely a Pydantic `Literal` or enum-constrained
        # field. Testing rejection of an unrecognized value ("unknown") confirms the
        # constraint is enforced at the model level rather than only in business
        # logic. Model-level validation is preferable because it catches bad inputs
        # before they reach any downstream code.
        with pytest.raises(ValidationError):
            PodHealthInput(cluster="prod-eastus", status_filter="unknown")

    def test_output_with_pods(self) -> None:
        # Note 16: The `PodDetail` nested model includes fields like `failure_category`
        # and `last_event` that encode diagnostic reasoning from the tool. Testing
        # that these fields survive construction and are accessible via the output
        # model verifies the schema supports the full diagnostic data the tool
        # is designed to return to the LLM.
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
        # Note 17: Asserting `output.groups["scheduling"] == 1` tests dictionary
        # field access on a Pydantic model. Some Pydantic configurations serialize
        # dict keys differently (e.g., converting to aliases). This assertion
        # confirms the `groups` field is accessible with its original string keys.
        assert output.groups["scheduling"] == 1

    def test_output_truncated(self) -> None:
        # Note 18: The `truncated` + `total_matching` combination is a pagination
        # contract. When a tool has more results than it can safely return to an LLM
        # (which has a context window limit), it truncates the list and sets these
        # flags. Testing the truncated state verifies the schema supports this
        # communication pattern correctly.
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
        # Note 19: `available_upgrades=["1.30.0"]` uses a realistic Kubernetes
        # version string. While the test does not validate the string format, using
        # realistic values makes the test double as documentation — readers can see
        # exactly what an upgrade version string looks like in this system.
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
        # Note 20: `assert "1.30.0" in output.available_upgrades` tests list
        # membership rather than exact equality. This is intentional — if the model
        # adds additional pre-populated upgrades (e.g., patch releases), the test
        # remains valid as long as "1.30.0" is present.
        assert "1.30.0" in output.available_upgrades


class TestUpgradeProgressModels:
    """Tests for UpgradeProgressInput and UpgradeProgressOutput."""

    def test_input_with_node_pool(self) -> None:
        # Note 21: `node_pool` is optional in upgrade progress — operators can ask
        # about all pools at once or a specific one. Testing the provided case first
        # ensures the optional field works when explicitly set, before testing the
        # omitted case below.
        inp = UpgradeProgressInput(cluster="prod-eastus", node_pool="userpool")
        assert inp.node_pool == "userpool"

    def test_input_node_pool_optional(self) -> None:
        # Note 22: Asserting `inp.node_pool is None` (not just falsy) ensures the
        # field defaults to `None` rather than an empty string `""`. Both are falsy
        # in Python, but the tool implementation likely checks `if inp.node_pool is
        # None` to decide whether to filter. A default of `""` would silently bypass
        # that check and cause incorrect behavior.
        inp = UpgradeProgressInput(cluster="prod-eastus")
        assert inp.node_pool is None

    def test_output_no_upgrade(self) -> None:
        # Note 23: `pod_transitions` is an optional nested model that is only
        # populated when an upgrade is actively in progress. Testing `assert
        # output.pod_transitions is None` (rather than just not asserting on it)
        # locks in the contract that a non-active upgrade does NOT include transition
        # data. If this contract breaks, code that conditionally checks
        # `output.pod_transitions` before accessing sub-fields would be bypassed,
        # causing `AttributeError` in production.
        output = UpgradeProgressOutput(
            cluster="prod-eastus",
            upgrade_in_progress=False,
            nodes=[],
            summary="No upgrade in progress for prod-eastus",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert output.upgrade_in_progress is False
        assert output.pod_transitions is None

    def test_output_with_pod_transitions(self) -> None:
        # Note 24: This test builds a complete, deeply nested object graph:
        # UpgradeProgressOutput -> PodTransitionSummary -> [AffectedPod]. Each
        # level is constructed explicitly, which verifies that Pydantic correctly
        # validates and stores nested model instances (not just primitive fields).
        # Building the objects in bottom-up order (pod first, then transitions,
        # then output) makes the data flow readable.
        pod = AffectedPod(
            name="web-abc",
            namespace="default",
            phase="Pending",
            reason="Unschedulable",
            node_name="node-1",
        )
        transitions = PodTransitionSummary(
            pending_count=3,
            failed_count=1,
            # Note 25: `by_category` uses two distinct category keys to verify that
            # dict values with multiple keys are stored correctly. A single-key dict
            # would not catch a bug where only the first key is preserved.
            by_category={"scheduling": 3, "runtime": 1},
            affected_pods=[pod],
            total_affected=4,
        )
        output = UpgradeProgressOutput(
            cluster="prod-eastus",
            upgrade_in_progress=True,
            nodes=[],
            pod_transitions=transitions,
            summary="prod-eastus: 0/0 nodes upgraded",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert output.pod_transitions is not None
        assert output.pod_transitions.pending_count == 3
        assert output.pod_transitions.failed_count == 1
        assert len(output.pod_transitions.affected_pods) == 1
        # Note 26: Drilling into `affected_pods[0].name` is a deep access test. It
        # verifies that the list of nested models was stored correctly, that
        # list indexing works on Pydantic list fields, and that the nested model's
        # fields are accessible — all in one assertion.
        assert output.pod_transitions.affected_pods[0].name == "web-abc"

    def test_pod_transition_summary_defaults(self) -> None:
        # Note 27: Constructing `PodTransitionSummary()` with no arguments tests
        # the all-defaults path. This verifies that the model can be created in an
        # "empty" state, which is useful when initialising a summary object before
        # iterating over pods to populate it. If any field lacks a default, this
        # test will raise a ValidationError, alerting the developer.
        summary = PodTransitionSummary()
        assert summary.pending_count == 0
        assert summary.failed_count == 0
        # Note 28: `== {}` checks that the default for a dict field is an empty dict,
        # not None. Pydantic uses `default_factory=dict` for mutable defaults to
        # avoid the shared-mutable-default pitfall. This assertion confirms that
        # factory is correctly configured.
        assert summary.by_category == {}
        assert summary.affected_pods == []
        assert summary.total_affected == 0

    def test_affected_pod_serialization(self) -> None:
        # Note 29: `model_dump()` is tested on `AffectedPod` specifically because
        # this model is likely serialised to JSON when the MCP tool returns results.
        # Checking `data["node_name"]` (with underscore) verifies that Pydantic is
        # not applying camelCase aliasing (`nodeName`) that would break consumers
        # expecting snake_case field names.
        pod = AffectedPod(
            name="api-xyz",
            namespace="payments",
            phase="Failed",
            reason="OOMKilled",
            node_name="node-2",
        )
        data = pod.model_dump()
        assert data["name"] == "api-xyz"
        assert data["node_name"] == "node-2"


class TestUpgradeDurationModels:
    """Tests for UpgradeDurationInput and UpgradeDurationOutput."""

    def test_input_defaults(self) -> None:
        # Note 30: `history_count` controls how many past upgrade runs are returned.
        # The default of 5 is a balance between providing enough historical context
        # for trend analysis and not overwhelming an LLM with more data than fits
        # comfortably in its context window. Testing the default documents this
        # intentional design choice.
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool")
        assert inp.history_count == 5

    def test_input_custom_history_count(self) -> None:
        # Note 31: Testing an override value (3, not the default 5) confirms that
        # the field accepts user-provided values and stores them correctly. If the
        # field were read-only or validator logic forced it back to the default, this
        # assertion would catch that regression.
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=3)
        assert inp.history_count == 3

    def test_output_structure(self) -> None:
        # Note 32: `current_run=None` tests the case where no upgrade is actively
        # running. This is the most common state (clusters spend far more time idle
        # than upgrading). Asserting `output.current_run is None` verifies the
        # optional field correctly represents absence of data rather than being
        # initialised to a zero-value object.
        output = UpgradeDurationOutput(
            cluster="prod-eastus",
            node_pool="userpool",
            current_run=None,
            historical=[],
            summary="No active upgrade; no historical data",
            timestamp="2026-02-28T12:00:00Z",
        )
        assert output.current_run is None
        # Note 33: `== []` (not just falsy) distinguishes an empty list from None.
        # A field that defaults to None instead of [] would cause `len(output.historical)`
        # to raise a TypeError in tool code that iterates unconditionally. Asserting
        # the exact empty-list value prevents this class of bug.
        assert output.historical == []


class TestPdbCheckModels:
    """Tests for PdbCheckInput and PdbCheckOutput."""

    def test_input_preflight_default(self) -> None:
        # Note 34: "preflight" is the safe default mode because it performs read-only
        # analysis before an upgrade starts. Making it the default means operators
        # who omit the `mode` field get the safer, less disruptive behavior. Testing
        # the default enforces this safety property at the schema level.
        inp = PdbCheckInput(cluster="prod-eastus")
        assert inp.mode == "preflight"
        # Note 35: Asserting `node_pool is None` alongside `mode == "preflight"`
        # in the same test is acceptable because both are defaults for the same
        # constructor call. They are conceptually related (the "no arguments" state
        # of PdbCheckInput), so testing them together reduces test count without
        # sacrificing clarity.
        assert inp.node_pool is None

    def test_input_live_mode(self) -> None:
        # Note 36: "live" mode is tested as an explicit override. This verifies the
        # default does not "stick" (i.e., that the field is not accidentally hardcoded
        # to "preflight" in the validator). The test is minimal by design — if the
        # field is stored correctly, there is nothing else to verify for this scenario.
        inp = PdbCheckInput(cluster="prod-eastus", mode="live")
        assert inp.mode == "live"

    def test_input_invalid_mode(self) -> None:
        # Note 37: PDB check mode validation mirrors the `validate_mode` function
        # tested in `test_validation.py`, but this test operates at the Pydantic
        # model layer. It is valid to have both: the validation tests verify the
        # helper function's logic, while this test verifies the model correctly
        # wires up that validator. Both layers must work for the system to be safe.
        with pytest.raises(ValidationError):
            PdbCheckInput(cluster="prod-eastus", mode="invalid")

    def test_output_with_risks(self) -> None:
        # Note 38: `PdbRisk.reason = "maxUnavailable=0"` is the most common and
        # dangerous PDB configuration — it means zero pods of a deployment can be
        # unavailable during a drain, which would block node upgrades indefinitely.
        # Using this realistic risk reason makes the test serve as documentation of
        # the PDB risk detection feature's primary use case.
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

    # Note 39: Scrubbing tests are security tests. They verify that sensitive
    # infrastructure details (IP addresses, Azure subscription IDs, FQDNs) are
    # removed from text before it is returned to an LLM or operator. Leaking these
    # values could enable privilege escalation or targeted attacks. Each test covers
    # a distinct category of sensitive data rather than a single omnibus test,
    # so failures pinpoint which scrubbing rule broke.

    def test_scrub_internal_ip(self) -> None:
        # Note 40: The IP "10.240.0.5" is in the RFC 1918 private range commonly
        # used by AKS pod and node networking. Testing with a specific realistic
        # address (not "1.2.3.4") ensures the scrubber handles AKS network ranges,
        # not just generic IPs. The negative assertion (`not in scrubbed`) combined
        # with the positive assertion (`[REDACTED_IP] in scrubbed`) verifies both
        # that the value was removed AND that it was replaced with a meaningful
        # placeholder (not just deleted, which would produce malformed text).
        text = "Node 10.240.0.5 is not ready"
        scrubbed = scrub_sensitive_values(text)
        assert "10.240.0.5" not in scrubbed
        assert "[REDACTED_IP]" in scrubbed

    def test_scrub_subscription_id(self) -> None:
        # Note 41: Azure subscription IDs follow the UUID v4 format
        # (8-4-4-4-12 hex characters). The scrubber must recognise this pattern
        # within a larger URL path ("/subscriptions/<uuid>/resourceGroups/...").
        # Testing within a realistic ARM URL ensures the regex handles the real
        # context where subscription IDs appear, not just an isolated UUID string.
        text = "Subscription /subscriptions/12345678-1234-1234-1234-123456789abc/resourceGroups/rg-prod"
        scrubbed = scrub_sensitive_values(text)
        assert "12345678-1234-1234-1234-123456789abc" not in scrubbed

    def test_preserve_node_names(self) -> None:
        # Note 42: This is a "do no harm" test — it verifies the scrubber does NOT
        # remove legitimate operational data. Node names like "aks-userpool-00000001"
        # contain numbers and hyphens that could be misidentified as parts of an IP
        # or UUID by an overly aggressive regex. Preserving them is essential for
        # diagnoses that reference specific nodes.
        text = "Node aks-userpool-00000001 is ready"
        scrubbed = scrub_sensitive_values(text)
        assert "aks-userpool-00000001" in scrubbed

    def test_scrub_resource_group(self) -> None:
        # Note 43: Resource group names encode environment and region information
        # (e.g., "rg-prod-eastus"). While not strictly secret, they are Azure
        # resource path components that could be used to enumerate infrastructure.
        # The scrubber removes them when they appear inside `/resourceGroups/` path
        # segments, which is the standard ARM URL pattern.
        text = "/subscriptions/abc123/resourceGroups/rg-prod-eastus/providers/foo"
        scrubbed = scrub_sensitive_values(text)
        assert "rg-prod-eastus" not in scrubbed

    def test_scrub_empty_string(self) -> None:
        # Note 44: The empty string is a boundary condition for string processing
        # functions. Regex operations on `""` can sometimes raise exceptions or
        # return unexpected matches (e.g., a regex that matches zero-length strings
        # could produce an infinite loop). This test verifies the function handles
        # the empty case gracefully and returns `""` unchanged.
        assert scrub_sensitive_values("") == ""

    def test_scrub_no_sensitive_data(self) -> None:
        # Note 45: The "no-op" test verifies the scrubber does not corrupt innocent
        # text. If the scrubber unconditionally replaces patterns that overlap with
        # normal text (e.g., any sequence of digits), it would mangle pod counts,
        # percentage values, or timestamps. The input "All 3 pods are healthy" is
        # chosen because it contains a number that should NOT be scrubbed.
        text = "All 3 pods are healthy"
        assert scrub_sensitive_values(text) == text

    def test_scrub_aks_fqdn(self) -> None:
        # Note 46: AKS cluster FQDNs follow the pattern `<name>.azmk8s.io`. These
        # are the public DNS endpoints used by `kubectl` and CI systems to reach the
        # Kubernetes API server. Exposing them in tool output could make the cluster
        # API server a target. `[REDACTED_FQDN]` is the replacement token, distinct
        # from `[REDACTED_HOST]`, which allows consumers to know what category of
        # data was removed.
        text = "Connected to aks-prod.eastus.azmk8s.io"
        scrubbed = scrub_sensitive_values(text)
        assert "azmk8s.io" not in scrubbed
        assert "[REDACTED_FQDN]" in scrubbed

    def test_scrub_vault_hostname(self) -> None:
        # Note 47: Azure Key Vault hostnames (`<name>.vault.azure.net`) expose the
        # vault name, which combined with a subscription ID could let an attacker
        # enumerate vault contents. The scrubber replaces the entire hostname with
        # `[REDACTED_HOST]`, preventing correlation attacks while preserving enough
        # context ("a vault hostname appeared here") for diagnostic purposes.
        text = "Access denied for myvault.vault.azure.net"
        scrubbed = scrub_sensitive_values(text)
        assert "myvault.vault.azure.net" not in scrubbed
        assert "[REDACTED_HOST]" in scrubbed

    def test_scrub_blob_hostname(self) -> None:
        # Note 48: Azure Blob Storage hostnames (`<account>.blob.core.windows.net`)
        # expose the storage account name. Storage accounts can contain sensitive
        # data and their names are used in shared access signature (SAS) URLs.
        # Scrubbing blob hostnames prevents accidental disclosure of storage account
        # names that could be used to brute-force SAS tokens or enumerate containers.
        text = "Storage at myaccount.blob.core.windows.net"
        scrubbed = scrub_sensitive_values(text)
        assert "myaccount.blob.core.windows.net" not in scrubbed
        assert "[REDACTED_HOST]" in scrubbed


class TestInputValidationBounds:
    """Tests for input model field constraints."""

    # Note 49: Boundary value tests (also called "off-by-one" tests) are a systematic
    # technique for testing numeric constraints. For a field with `ge=1, le=1440`,
    # the boundary set is: {0 (invalid), 1 (valid min), 1440 (valid max), 1441 (invalid)}.
    # Each boundary gets its own test case because bugs are most likely to occur at
    # boundaries, not in the middle of the valid range.

    def test_lookback_minutes_default(self) -> None:
        # Note 50: The 30-minute default for `lookback_minutes` represents a
        # reasonable sliding window for detecting recently-failed or recently-pending
        # pods. Too short (< 5 minutes) might miss slow-restarting pods; too long
        # (hours) might return so many historical pods that the output exceeds LLM
        # context limits. The test documents this design decision.
        inp = PodHealthInput(cluster="prod-eastus")
        assert inp.lookback_minutes == 30

    def test_lookback_minutes_valid(self) -> None:
        # Note 51: 60 minutes is tested as a valid non-default value. This lies
        # comfortably within the valid range, verifying that the constraint validator
        # accepts values other than the default (ruling out an accidental hardcoded
        # equality check rather than a range check).
        inp = PodHealthInput(cluster="prod-eastus", lookback_minutes=60)
        assert inp.lookback_minutes == 60

    def test_lookback_minutes_too_high(self) -> None:
        # Note 52: 1441 is exactly one above the maximum (1440 = 24 hours). This
        # tests the upper boundary exclusion. If the constraint were `le=1441`
        # instead of `le=1440`, this test would fail, catching an off-by-one error
        # in the schema definition. Using 1441 (not 9999) makes the intent of
        # "boundary" clear.
        with pytest.raises(ValidationError):
            PodHealthInput(cluster="prod-eastus", lookback_minutes=1441)

    def test_lookback_minutes_too_low(self) -> None:
        # Note 53: 0 tests the lower boundary exclusion. The constraint is likely
        # `ge=1` (greater than or equal to 1), meaning 0 must be rejected. A
        # lookback of 0 minutes would return no pods or cause a division-by-zero
        # error in code that computes rates over the window. The schema constraint
        # prevents this invalid state from reaching business logic.
        with pytest.raises(ValidationError):
            PodHealthInput(cluster="prod-eastus", lookback_minutes=0)

    def test_history_count_default(self) -> None:
        # Note 54: The default of 5 history records provides a trend sample without
        # excessive data transfer or LLM context consumption. This test pins the
        # default to protect against accidental changes during refactoring.
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool")
        assert inp.history_count == 5

    def test_history_count_valid(self) -> None:
        # Note 55: 50 is the maximum valid value (just below the upper limit).
        # Testing at the maximum valid value (rather than an arbitrary middle value
        # like 10) gives confidence that the upper constraint is `le=50` and not
        # something smaller like `le=20` that would reject this input.
        inp = UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=50)
        assert inp.history_count == 50

    def test_history_count_too_high(self) -> None:
        # Note 56: 51 is one above the maximum (50). This tests the upper boundary
        # exclusion symmetrically with `test_history_count_valid`. The pair of tests
        # (50 accepted, 51 rejected) precisely locates the constraint boundary and
        # will catch any change to the `le` validator argument.
        with pytest.raises(ValidationError):
            UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=51)

    def test_history_count_too_low(self) -> None:
        # Note 57: 0 is below the minimum of 1. Requesting 0 history records is
        # semantically meaningless and likely a programming error (an uninitialised
        # variable defaulting to 0). Rejecting it at the schema level ensures the
        # tool implementation never receives a nonsensical history count and does
        # not need to handle the 0-record edge case in its logic.
        with pytest.raises(ValidationError):
            UpgradeDurationInput(cluster="prod-eastus", node_pool="userpool", history_count=0)
