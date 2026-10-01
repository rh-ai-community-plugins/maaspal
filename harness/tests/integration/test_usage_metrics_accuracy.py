"""Integration test for the usage_metrics_accuracy scenario."""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MAAS_API_URL"),
    reason="MAAS_API_URL not set — requires a live RHOAI cluster",
)


async def test_metrics_fill_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/usage_metrics_accuracy.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"usage_metrics_accuracy returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )


async def test_metrics_fill_maas_counts_match_sent() -> None:
    """MaaS-reported request/token deltas must match what the harness actually sent.

    Requires MAAS_METRICS_URL to be configured on the cluster — see ADR-014/ADR-015
    (the scenario's own metrics_queries: block supplies the PromQL). If unset, the
    background poller never populates
    shared_state["metrics"] and both assertions stay PENDING (not FAILING), so this
    only asserts once the metrics pipeline is actually wired up.
    """
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/usage_metrics_accuracy.yaml", run_id)
    result = await runner.run()

    assertions = {a.name: a for a in result.assertions}

    for name in ("maas_requests_match", "maas_tokens_match"):
        ar = assertions.get(name)
        if ar is None or ar.status == "PENDING":
            continue
        assert ar.status == "PASSING", (
            f"{name}: MaaS-reported value {ar.current_value} did not match "
            f"expected {ar.expected_value} within tolerance"
        )
