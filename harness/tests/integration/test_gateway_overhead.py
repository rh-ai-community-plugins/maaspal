"""Integration test for the gateway_overhead scenario."""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("MAAS_API_URL") and os.environ.get("DIRECT_TARGET_URL")),
    reason="MAAS_API_URL and DIRECT_TARGET_URL not set — requires a live cluster",
)


async def test_direct_inference_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    # Scenario reads target_url and target_token from env via config interpolation
    runner = ScenarioRunner("scenarios/gateway_overhead.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"gateway_overhead returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )


async def test_direct_inference_no_api_keys_created() -> None:
    """gateway_overhead must not create or leave any MaaS API keys."""
    import httpx

    maas_url = os.environ["MAAS_API_URL"]
    sa_token = (
        open("/var/run/secrets/kubernetes.io/serviceaccount/token").read()
        if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")
        else os.environ.get("SA_TOKEN", "")
    )

    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/gateway_overhead.yaml", run_id)
    await runner.run()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{maas_url}/maas-api/v1/api-keys/search",
            json={"name_prefix": "maaspal-"},
            headers={"Authorization": f"Bearer {sa_token}"},
        )
        resp.raise_for_status()
        keys = resp.json().get("items", [])

    assert len(keys) == 0, f"gateway_overhead should create no keys; found: {keys}"
