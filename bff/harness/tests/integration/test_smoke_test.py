"""Integration test for the smoke_test scenario."""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MAAS_API_URL"),
    reason="MAAS_API_URL not set — requires a live RHOAI cluster",
)


async def test_platform_health_check_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/smoke_test.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"smoke_test returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )
