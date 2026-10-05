"""Browser hand-off routes (api/routes/browser.py) — the open run page claims a
"Send from user browser" step and reports what it sent."""

import json
from pathlib import Path

import aiosqlite
import pytest

from harness import browser_channel

_ORDER = {
    "order_id": "o1", "result_key": "inference_results", "step": "send_requests",
    "targets": [{"url": "http://maas.test/v1", "model": "m", "key": "sk-oai-secret"}], "plan": [0, 0],
}


@pytest.fixture()
def results_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path / "results"


@pytest.fixture()
async def client(results_dir):
    from httpx import ASGITransport, AsyncClient

    from api.db import get_db_path, init_db
    from api.main import app

    await init_db()
    async with aiosqlite.connect(get_db_path()) as db:
        for run_id, status in (("run-1", "RUNNING"), ("done-1", "PASS")):
            await db.execute(
                "INSERT INTO runs (id, scenario, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (run_id, "smoke_test", status, "t", "t"),
            )
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_nothing_waiting(client) -> None:
    resp = await client.get("/api/runs/run-1/browser-work")
    assert resp.json() == {"pending": False}
    assert (await client.post("/api/runs/run-1/browser-work/claim", json={})).status_code == 404


async def test_status_poll_never_carries_keys(client, results_dir) -> None:
    browser_channel.write_order(results_dir, "run-1", "inference_results", _ORDER)
    resp = await client.get("/api/runs/run-1/browser-work")
    assert resp.json() == {
        "pending": True, "claimed": False, "step": "send_requests",
        "result_key": "inference_results", "count": 2,
    }
    assert "sk-oai" not in resp.text
    assert resp.headers["cache-control"] == "no-store"


async def test_one_tab_claims_then_reports(client, results_dir) -> None:
    browser_channel.write_order(results_dir, "run-1", "inference_results", _ORDER)
    claim = await client.post("/api/runs/run-1/browser-work/claim", json={"origin": "https://rh-ai.test"})
    assert claim.status_code == 200
    order = claim.json()
    assert order["targets"][0]["key"] == "sk-oai-secret"
    assert claim.headers["cache-control"] == "no-store"
    # A second tab can't send the same step twice.
    assert (await client.post("/api/runs/run-1/browser-work/claim", json={})).status_code == 409
    assert (await client.get("/api/runs/run-1/browser-work")).json()["claimed"] is True

    resp = await client.post("/api/runs/run-1/browser-results", json={
        "claim_id": order["claim_id"], "result_key": "inference_results",
        "records": [{
            "t0_offset_s": 0, "latency_ms": 12, "ok": True, "status": 200,
            "headers": {"Server": "envoy", "Set-Cookie": "x"}, "usage": {"total_tokens": 5},
        }],
        "done": True,
    })
    assert resp.status_code == 204
    lines = [json.loads(x) for x in browser_channel.records_path(
        results_dir, "run-1", "inference_results").read_text().splitlines()]
    assert lines[0]["headers"] == {"server": "envoy"}  # only what failure_origin() reads
    assert lines[1] == {"done": True, "reason": "finished", "detail": ""}


async def test_reports_need_the_claim(client, results_dir) -> None:
    browser_channel.write_order(results_dir, "run-1", "inference_results", _ORDER)
    await client.post("/api/runs/run-1/browser-work/claim", json={})
    resp = await client.post("/api/runs/run-1/browser-results", json={
        "claim_id": "not-mine", "result_key": "inference_results", "records": [],
    })
    assert resp.status_code == 410


async def test_only_while_the_run_is_running(client, results_dir) -> None:
    browser_channel.write_order(results_dir, "done-1", "inference_results", _ORDER)
    assert (await client.post("/api/runs/done-1/browser-work/claim", json={})).status_code == 409
    assert (await client.post("/api/runs/nope/browser-work/claim", json={})).status_code == 404


async def test_record_batches_are_capped(client, results_dir) -> None:
    browser_channel.write_order(results_dir, "run-1", "inference_results", _ORDER)
    order = (await client.post("/api/runs/run-1/browser-work/claim", json={})).json()
    resp = await client.post("/api/runs/run-1/browser-results", json={
        "claim_id": order["claim_id"], "result_key": "inference_results",
        "records": [{"t0_offset_s": 0, "latency_ms": 1}] * 501,
    })
    assert resp.status_code == 422


async def test_gated_like_every_other_route(client, monkeypatch) -> None:
    monkeypatch.setenv("MAASPAL_AUTH_MODE", "on")
    assert (await client.get("/api/runs/run-1/browser-work")).status_code == 401
    assert (await client.post("/api/runs/run-1/browser-work/claim", json={})).status_code == 401
