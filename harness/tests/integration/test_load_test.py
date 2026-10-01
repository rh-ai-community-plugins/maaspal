"""Integration test for the load_test scenario.

Requires a live RHOAI cluster — skipped automatically unless MAAS_API_URL is set.
Run with:
    MAAS_API_URL=https://maas.<cluster-domain> pytest harness/tests/integration/ -v
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MAAS_API_URL"),
    reason="MAAS_API_URL not set — requires a live RHOAI cluster",
)


async def test_single_key_load_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/load_test.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"load_test returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )
    assert any(t.status == "PASS" for t in result.tasks)
    assert all(a.status in ("PASSING", "PENDING") for a in result.assertions)


async def test_single_key_load_no_leftover_keys() -> None:
    """Verify cleanup revokes all provisioned keys."""
    import httpx

    maas_url = os.environ["MAAS_API_URL"]
    sa_token = (
        open("/var/run/secrets/kubernetes.io/serviceaccount/token").read()
        if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token")
        else os.environ.get("SA_TOKEN", "")
    )

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/load_test.yaml", run_id)  # type: ignore[name-defined]
    await runner.run()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{maas_url}/maas-api/v1/api-keys/search",
            json={"name_prefix": "maaspal-load-key"},
            headers={"Authorization": f"Bearer {sa_token}"},
        )
        resp.raise_for_status()
        keys = resp.json().get("items", [])

    assert len(keys) == 0, f"Expected no leftover keys, found: {keys}"
