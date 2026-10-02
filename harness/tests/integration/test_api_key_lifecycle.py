"""Integration test for the api_key_lifecycle scenario."""
import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MAAS_API_URL"),
    reason="MAAS_API_URL not set — requires a live RHOAI cluster",
)


async def test_api_key_lifecycle_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/api_key_lifecycle.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"api_key_lifecycle returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )


async def test_api_key_lifecycle_no_leftover_keys() -> None:
    """All keys provisioned must be gone after the run — revoked mid-scenario
    by revoke_api_keys, then cleanup's own DELETE harmlessly no-ops on them.
    """
    import httpx

    maas_url = os.environ["MAAS_API_URL"]
    sa_token = (
        Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text()
        if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")
        else os.environ.get("SA_TOKEN", "")
    )

    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/api_key_lifecycle.yaml", run_id)
    await runner.run()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{maas_url}/maas-api/v1/api-keys/search",
            json={"name_prefix": "maaspal-lifecycle-key"},
            headers={"Authorization": f"Bearer {sa_token}"},
        )
        resp.raise_for_status()
        keys = resp.json().get("items", [])

    assert len(keys) == 0, f"Expected no leftover keys, found: {keys}"
