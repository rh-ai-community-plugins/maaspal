"""API route integration tests — use in-memory SQLite and mock K8s."""
import json

import aiosqlite
import pytest


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))


@pytest.fixture()
def _mock_k8s(monkeypatch) -> None:
    import api.routes.logs
    import api.routes.runs

    monkeypatch.setattr(
        api.routes.runs, "create_job",
        lambda scenario, run_id, config_overrides=None: None,
    )
    # Log capture is a background thread that talks to a real cluster — not
    # available in this test environment. get_log_lines() reading a nonexistent
    # file already safely returns ([], False), so only this needs stubbing.
    monkeypatch.setattr(api.routes.logs, "ensure_log_capture", lambda run_id: None)


@pytest.fixture()
async def client(_temp_db, _mock_k8s):
    from httpx import ASGITransport, AsyncClient

    from api.db import init_db
    from api.main import app

    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_scenarios_returns_all_fifteen_with_metadata(client) -> None:
    resp = await client.get("/api/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 15
    by_name = {s["name"]: s for s in data}
    assert {"smoke_test", "verify_subscription", "verify_subscription_rate_limit", "load_test"} <= set(by_name)
    for s in data:
        for field in ("title", "summary", "description", "category", "kind", "inputs", "previous_names"):
            assert field in s, f"{s['name']} missing {field}"

    rate_limit = by_name["verify_subscription_rate_limit"]
    assert rate_limit["title"] == "Is my subscription's rate limit enforced?"
    assert rate_limit["kind"] == "verify"
    assert set(rate_limit["previous_names"]) == {
        "rate_limit_validation", "rate_limit_validation_existing_subscription",
    }
    # The limit itself is read off the subscription at run time, not typed in.
    assert "token_limit" not in rate_limit["config"]
    assert rate_limit["inputs"]["limitador_namespace"]["from_model"] == "http_route"


async def test_scenario_metadata_defaults_for_a_bare_custom_scenario(client, tmp_path, monkeypatch) -> None:
    """A user-written scenario with none of the display metadata still lists
    cleanly: title from its name, kind "verify", empty lists."""
    (tmp_path / "my_check.yaml").write_text(
        "name: my_check\ndescription: test\ntasks: []\ncleanup: automatic\n"
    )
    monkeypatch.setenv("SCENARIOS_DIR", str(tmp_path))

    [scenario] = (await client.get("/api/scenarios")).json()
    assert scenario["title"] == "My Check"
    assert scenario["kind"] == "verify"
    assert scenario["requires"] == [] and scenario["inputs"] == {}


async def test_scenarios_category_defaults_to_custom(client, tmp_path, monkeypatch) -> None:
    """A scenario file with no `category:` field lands in "Custom" — the one
    default that makes the UI's always-present Custom bucket work for
    anything a user drops in later without extra plumbing."""
    (tmp_path / "no_category.yaml").write_text(
        "name: no_category\ndescription: test\ntasks: []\ncleanup: automatic\n"
    )
    monkeypatch.setenv("SCENARIOS_DIR", str(tmp_path))

    resp = await client.get("/api/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert [(s["name"], s["category"]) for s in data] == [("no_category", "Custom")]


async def test_scenarios_not_shipped_are_marked_custom(client, tmp_path, monkeypatch) -> None:
    """Any scenario that isn't a built-in is custom. It keeps a built-in
    category it names; an unknown category lands in "Custom"."""
    for name, category in [("smoke_test", "Quick check"), ("team_limits", "Rate limits"), ("nightly", "Team")]:
        (tmp_path / f"{name}.yaml").write_text(
            f"name: {name}\ncategory: {category}\ndescription: test\ntasks: []\n"
        )
    monkeypatch.setenv("SCENARIOS_DIR", str(tmp_path))

    data = {s["name"]: (s["category"], s["custom"]) for s in (await client.get("/api/scenarios")).json()}
    assert data == {
        "smoke_test": ("Quick check", False),
        "team_limits": ("Rate limits", True),
        "nightly": ("Custom", True),
    }


async def test_create_run_returns_run_id(client) -> None:
    resp = await client.post("/api/runs", json={"scenario": "load_test"})
    assert resp.status_code == 201
    data = resp.json()
    assert "run_id" in data
    assert data["scenario"] == "load_test"


async def test_list_runs_includes_created_run(client) -> None:
    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()]
    assert run_id in ids


async def test_get_run_by_id(client) -> None:
    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id
    assert resp.json()["scenario"] == "load_test"


async def test_get_nonexistent_run_returns_404(client) -> None:
    resp = await client.get("/api/runs/does-not-exist")
    assert resp.status_code == 404


async def test_get_run_log_lines(client) -> None:
    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.get(f"/api/runs/{run_id}/logs/lines")
    assert resp.status_code == 200
    assert resp.json() == {"lines": [], "done": False, "next_offset": 0}


async def test_stop_pending_run_finalizes_immediately(client, monkeypatch) -> None:
    """No pod exists yet (stop_run returns False) — the route finalizes CANCELLED
    itself, since no harness process will ever self-report for a run that never
    got a pod."""
    import api.routes.runs

    monkeypatch.setattr(api.routes.runs, "stop_run", lambda run_id: False)

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.post(f"/api/runs/{run_id}/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"

    get_resp = await client.get(f"/api/runs/{run_id}")
    assert get_resp.json()["status"] == "CANCELLED"


async def test_stop_running_run_returns_stopping_without_finalizing(client, monkeypatch) -> None:
    """A pod exists (stop_run returns True) — the route must NOT write the DB row
    itself, to avoid racing api/main.py's _sync_completed_runs poller, which is
    the single source of truth for every other terminal transition too."""
    import api.routes.runs

    monkeypatch.setattr(api.routes.runs, "stop_run", lambda run_id: True)

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.post(f"/api/runs/{run_id}/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "STOPPING"

    get_resp = await client.get(f"/api/runs/{run_id}")
    assert get_resp.json()["status"] == "RUNNING"


async def test_stop_unknown_run_returns_404(client) -> None:
    resp = await client.post("/api/runs/does-not-exist/stop")
    assert resp.status_code == 404


async def test_stop_already_terminal_run_returns_409(client) -> None:
    from api.db import get_db_path

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("UPDATE runs SET status='PASS' WHERE id=?", (run_id,))
        await db.commit()

    resp = await client.post(f"/api/runs/{run_id}/stop")
    assert resp.status_code == 409


async def test_get_run_config_before_written_returns_null(client) -> None:
    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.get(f"/api/runs/{run_id}/config")
    assert resp.status_code == 200
    assert resp.json() == {"config_yaml": None}


async def test_get_run_config_returns_yaml(client, tmp_path, monkeypatch) -> None:
    import api.routes.config

    monkeypatch.setattr(api.routes.config, "_DATA_DIR", tmp_path)
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    (tmp_path / "results" / f"{run_id}-config.json").write_text(
        json.dumps({"request_count": 5, "api_token": "***REDACTED***"})
    )

    resp = await client.get(f"/api/runs/{run_id}/config")
    assert resp.status_code == 200
    yaml_text = resp.json()["config_yaml"]
    assert "request_count: 5" in yaml_text
    assert "***REDACTED***" in yaml_text


async def test_create_run_defaults_auto_cleanup_true(client, tmp_path, monkeypatch) -> None:
    import api.routes.runs

    monkeypatch.setattr(api.routes.runs, "_RESULTS_DIR", tmp_path)

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    get_resp = await client.get(f"/api/runs/{run_id}")
    assert get_resp.json()["auto_cleanup"] is True

    flag = json.loads((tmp_path / f"{run_id}-cleanup-flag.json").read_text())
    assert flag["auto_cleanup"] is True


async def test_create_run_respects_auto_cleanup_false(client, tmp_path, monkeypatch) -> None:
    import api.routes.runs

    monkeypatch.setattr(api.routes.runs, "_RESULTS_DIR", tmp_path)

    r = await client.post(
        "/api/runs", json={"scenario": "load_test", "auto_cleanup": False}
    )
    run_id = r.json()["run_id"]

    get_resp = await client.get(f"/api/runs/{run_id}")
    assert get_resp.json()["auto_cleanup"] is False

    flag = json.loads((tmp_path / f"{run_id}-cleanup-flag.json").read_text())
    assert flag["auto_cleanup"] is False


async def test_set_auto_cleanup_toggle_while_active(client, tmp_path, monkeypatch) -> None:
    import api.routes.runs

    monkeypatch.setattr(api.routes.runs, "_RESULTS_DIR", tmp_path)

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.post(f"/api/runs/{run_id}/auto-cleanup", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"auto_cleanup": False}

    get_resp = await client.get(f"/api/runs/{run_id}")
    assert get_resp.json()["auto_cleanup"] is False

    flag = json.loads((tmp_path / f"{run_id}-cleanup-flag.json").read_text())
    assert flag["auto_cleanup"] is False


async def test_set_auto_cleanup_404_unknown_run(client) -> None:
    resp = await client.post("/api/runs/does-not-exist/auto-cleanup", json={"enabled": False})
    assert resp.status_code == 404


async def test_set_auto_cleanup_409_once_terminal(client) -> None:
    from api.db import get_db_path

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("UPDATE runs SET status='PASS' WHERE id=?", (run_id,))
        await db.commit()

    resp = await client.post(f"/api/runs/{run_id}/auto-cleanup", json={"enabled": True})
    assert resp.status_code == 409


async def test_cleanup_now_404_unknown_run(client) -> None:
    resp = await client.post("/api/runs/does-not-exist/cleanup")
    assert resp.status_code == 404


async def test_cleanup_now_409_while_active(client) -> None:
    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    resp = await client.post(f"/api/runs/{run_id}/cleanup")
    assert resp.status_code == 409


async def test_cleanup_now_409_when_already_done(client) -> None:
    from api.db import get_db_path

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE runs SET status='PASS', cleanup_status='done' WHERE id=?", (run_id,)
        )
        await db.commit()

    resp = await client.post(f"/api/runs/{run_id}/cleanup")
    assert resp.status_code == 409


async def test_cleanup_now_starts_when_skipped(client, monkeypatch) -> None:
    import api.routes.runs
    from api.db import get_db_path

    started: list[str] = []

    async def _fake_manual_cleanup(run_id: str) -> None:
        started.append(run_id)

    monkeypatch.setattr(api.routes.runs, "run_manual_cleanup", _fake_manual_cleanup)

    r = await client.post("/api/runs", json={"scenario": "load_test"})
    run_id = r.json()["run_id"]

    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE runs SET status='FAIL', cleanup_status='skipped' WHERE id=?", (run_id,)
        )
        await db.commit()

    resp = await client.post(f"/api/runs/{run_id}/cleanup")
    assert resp.status_code == 200
    assert resp.json() == {"cleanup_status": "cleaning"}

    get_resp = await client.get(f"/api/runs/{run_id}")
    assert get_resp.json()["cleanup_status"] == "cleaning"

    # Let the fire-and-forget asyncio task actually run.
    import asyncio

    await asyncio.sleep(0)
    assert started == [run_id]
