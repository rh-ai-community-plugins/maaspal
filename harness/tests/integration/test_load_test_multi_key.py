"""Integration test for the load_test scenario with several keys (formerly multi_key_load)."""
import os
import uuid

import pytest


@pytest.fixture(autouse=True)
def _five_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAASPAL_CONFIG_OVERRIDES", '{"key_count": 5, "request_count": 5}')


pytestmark = pytest.mark.skipif(
    not os.environ.get("MAAS_API_URL"),
    reason="MAAS_API_URL not set — requires a live RHOAI cluster",
)


async def test_multi_key_load_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/load_test.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"load_test returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )


async def test_multi_key_load_all_keys_revoked() -> None:
    """All N keys provisioned must be revoked after the run."""
    import httpx

    maas_url = os.environ["MAAS_API_URL"]
    sa_token = (
        open("/var/run/secrets/kubernetes.io/serviceaccount/token").read()
        if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")
        else os.environ.get("SA_TOKEN", "")
    )

    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/load_test.yaml", run_id)
    await runner.run()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{maas_url}/maas-api/v1/api-keys/search",
            json={"name_prefix": "maaspal-multi-key"},
            headers={"Authorization": f"Bearer {sa_token}"},
        )
        resp.raise_for_status()
        keys = resp.json().get("items", [])

    assert len(keys) == 0, f"Expected no leftover keys, found: {keys}"
