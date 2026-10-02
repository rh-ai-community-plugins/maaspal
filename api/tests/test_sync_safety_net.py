"""A harness that dies without writing its result (SIGKILL after an overrun
grace period, OOMKill) must not leave its run RUNNING forever."""
import json

import aiosqlite
import pytest

import api.main as main_module
from api.db import get_db_path, init_db


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main_module, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(main_module, "delete_stopped_job", lambda run_id: None)
    main_module._dead_harness_seen.clear()


async def _running(run_id: str) -> None:
    await init_db()
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "INSERT INTO runs (id, scenario, status, created_at, updated_at) VALUES (?,?,?,?,?)",
            (run_id, "load_test", "RUNNING", "now", "now"),
        )
        await db.commit()


async def _status(run_id: str) -> tuple[str, str]:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute("SELECT status, cleanup_status FROM runs WHERE id=?", (run_id,)) as cur:
            return await cur.fetchone()


async def test_stopped_run_whose_harness_died_is_finalized_cancelled(tmp_path, monkeypatch) -> None:
    await _running("r1")
    (tmp_path / "r1-cleanup-status.json").write_text(json.dumps({"status": "done", "error": None}))
    monkeypatch.setattr(main_module, "run_job_state", lambda run_id: ("suspended", False))

    await main_module._sync_completed_runs()
    assert (await _status("r1"))[0] == "RUNNING"  # first sighting: wait one poll

    await main_module._sync_completed_runs()
    assert await _status("r1") == ("CANCELLED", "done")


async def test_failed_job_without_result_is_finalized_fail(monkeypatch) -> None:
    await _running("r2")
    monkeypatch.setattr(main_module, "run_job_state", lambda run_id: ("failed", False))
    await main_module._sync_completed_runs()
    await main_module._sync_completed_runs()
    assert (await _status("r2"))[0] == "FAIL"


@pytest.mark.parametrize("state", [("active", True), ("suspended", True), ("active", False), (None, False)])
async def test_live_or_unknown_runs_are_left_alone(monkeypatch, state) -> None:
    await _running("r3")
    monkeypatch.setattr(main_module, "run_job_state", lambda run_id: state)
    for _ in range(3):
        await main_module._sync_completed_runs()
    assert (await _status("r3"))[0] == "RUNNING"
