"""Importing custom scenarios from the UI (api/scenario_store.py)."""
import pytest

_VALID = """\
# a comment that must survive the import
name: my_smoke
title: "Is my thing up?"
description: test
category: Quick check
config:
  request_count: 3
inputs:
  request_count: {label: "Requests"}
requires: [request_count]
tasks:
  - name: check_platform_health
  - name: stub_pass
cleanup: automatic
"""


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))


@pytest.fixture()
async def client(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    import api.routes.runs
    from api.db import init_db
    from api.main import app

    calls: list[dict] = []
    monkeypatch.setattr(
        api.routes.runs, "create_job",
        lambda scenario, run_id, config_overrides=None, scenario_path=None: calls.append(
            {"scenario": scenario, "scenario_path": scenario_path}
        ),
    )
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.job_calls = calls  # type: ignore[attr-defined]
        yield c


async def test_import_lists_the_scenario_as_custom_and_imported(client, tmp_path) -> None:
    resp = await client.post("/api/scenarios/import", json={"yaml": _VALID})
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "my_smoke"

    data = {s["name"]: s for s in (await client.get("/api/scenarios")).json()}
    assert len(data) == 17
    mine = data["my_smoke"]
    assert (mine["custom"], mine["imported"], mine["category"]) == (True, True, "Quick check")
    assert data["smoke_test"]["imported"] is False
    # Stored verbatim, comments included.
    stored = (tmp_path / "imported-scenarios" / "my_smoke.yaml").read_text()
    assert stored == _VALID


@pytest.mark.parametrize(
    ("text", "status", "fragment"),
    [
        ("name: [unclosed", 400, "Not valid YAML"),
        ("- just\n- a list\n", 400, "must be a mapping"),
        ("name: Bad-Name\ntasks: [{name: stub_pass}]\n", 400, "lowercase"),
        ("name: stub_mine\ntasks: [{name: stub_pass}]\n", 400, "stub"),
        ("name: empty\ntasks: []\n", 400, "non-empty `tasks:`"),
        ("name: typo\ntasks: [{name: send_reqests}]\n", 400, "Unknown task(s): send_reqests"),
        ("name: k\nkind: maybe\ntasks: [{name: stub_pass}]\n", 400, "`kind: maybe`"),
        ("name: r\nrequires: [model]\ntasks: [{name: stub_pass}]\n", 400, "requires"),
        ("name: w\ntasks: [{name: stub_pass, when: oops}]\n", 400, "doesn't load"),
        ("name: smoke_test\ntasks: [{name: stub_pass}]\n", 409, "built-in"),
    ],
)
async def test_import_rejects_with_a_readable_reason(client, tmp_path, text, status, fragment) -> None:
    resp = await client.post("/api/scenarios/import", json={"yaml": text})
    assert resp.status_code == status
    assert fragment in resp.json()["detail"]
    # Nothing (not even a temp file) is left behind.
    imported = tmp_path / "imported-scenarios"
    assert not imported.exists() or list(imported.iterdir()) == []


async def test_reimport_needs_replace(client) -> None:
    assert (await client.post("/api/scenarios/import", json={"yaml": _VALID})).status_code == 201
    again = await client.post("/api/scenarios/import", json={"yaml": _VALID})
    assert again.status_code == 409
    assert "already called my_smoke" in again.json()["detail"]

    changed = _VALID.replace("Is my thing up?", "Still up?")
    replaced = await client.post("/api/scenarios/import", json={"yaml": changed, "replace": True})
    assert replaced.status_code == 201
    assert replaced.json()["title"] == "Still up?"


async def test_delete_only_removes_imported_scenarios(client) -> None:
    await client.post("/api/scenarios/import", json={"yaml": _VALID})
    assert (await client.delete("/api/scenarios/my_smoke")).status_code == 204
    names = {s["name"] for s in (await client.get("/api/scenarios")).json()}
    assert "my_smoke" not in names

    assert (await client.delete("/api/scenarios/my_smoke")).status_code == 404
    assert (await client.delete("/api/scenarios/smoke_test")).status_code == 404
    assert (await client.delete("/api/scenarios/..%2Fmaaspal")).status_code == 404


async def test_runs_read_imported_scenarios_from_the_pvc(client, tmp_path) -> None:
    await client.post("/api/scenarios/import", json={"yaml": _VALID})
    resp = await client.post("/api/runs", json={"scenario": "my_smoke"})
    assert resp.status_code == 201
    [call] = client.job_calls
    assert call["scenario"] == "my_smoke"
    assert call["scenario_path"] == str((tmp_path / "imported-scenarios" / "my_smoke.yaml").resolve())


async def test_running_an_unknown_scenario_is_a_404(client) -> None:
    resp = await client.post("/api/runs", json={"scenario": "nope"})
    assert resp.status_code == 404
    assert client.job_calls == []
