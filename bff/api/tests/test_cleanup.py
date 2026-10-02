"""Unit tests for api/cleanup.py's manual "Clean Up Now" execution — verifies
it reconstructs and calls the exact same Task.cleanup() methods the harness's
own end-of-run loop would have called, driven by persisted state instead of a
live in-memory shared_state.
"""
import json

import aiosqlite
import pytest

from api import cleanup as cleanup_module
from api.db import get_db_path
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))


async def _init_run_row(run_id: str) -> None:
    from api.db import init_db

    await init_db()
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "INSERT INTO runs (id, scenario, status, created_at, updated_at, cleanup_status) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, "test_scenario", "FAIL", "now", "now", "skipped"),
        )
        await db.commit()


async def test_run_manual_cleanup_calls_cleanup_with_empty_params_and_persisted_state(
    tmp_path, monkeypatch
) -> None:
    calls: list[tuple[str, dict, dict]] = []

    class _Recorder(Task):
        async def run(self, ctx: TaskContext):  # type: ignore[override]  # pragma: no cover - not exercised
            raise NotImplementedError

        async def cleanup(self, ctx: TaskContext) -> None:
            calls.append((self.name, dict(self.params), dict(ctx.shared_state)))

    REGISTRY["_cleanup_recorder"] = _Recorder
    monkeypatch.setattr(cleanup_module, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cleanup_module.maas_client, "_kube", lambda: None)
    monkeypatch.setattr(cleanup_module.maas_client, "sa_token", lambda: "test-token")
    monkeypatch.setattr(cleanup_module.maas_client, "maas_api_url", lambda: "https://maas.example.com")

    run_id = "manual-cleanup-001"
    await _init_run_row(run_id)

    (tmp_path / f"{run_id}-config.json").write_text(
        json.dumps({"tasks": [{"name": "_cleanup_recorder", "params": {"key_name": "should-be-ignored"}}]})
    )
    (tmp_path / f"{run_id}-cleanup-state.json").write_text(
        json.dumps({"api_keys": [{"id": "k1"}]})
    )

    try:
        await cleanup_module.run_manual_cleanup(run_id)

        assert len(calls) == 1
        name, params, shared_state = calls[0]
        assert name == "_cleanup_recorder"
        assert params == {}, "cleanup() must be driven by shared_state, not re-passed params"
        assert shared_state == {"api_keys": [{"id": "k1"}]}

        async with aiosqlite.connect(get_db_path()) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT cleanup_status FROM runs WHERE id=?", (run_id,)) as cur:
                row = await cur.fetchone()
        assert row is not None
        assert row["cleanup_status"] == "done"

        status = json.loads((tmp_path / f"{run_id}-cleanup-status.json").read_text())
        assert status["status"] == "done"
    finally:
        REGISTRY.pop("_cleanup_recorder", None)


async def test_run_manual_cleanup_marks_failed_when_a_task_raises(tmp_path, monkeypatch) -> None:
    class _Broken(Task):
        async def run(self, ctx: TaskContext):  # type: ignore[override]  # pragma: no cover - not exercised
            raise NotImplementedError

        async def cleanup(self, ctx: TaskContext) -> None:
            raise RuntimeError("boom")

    REGISTRY["_cleanup_broken"] = _Broken
    monkeypatch.setattr(cleanup_module, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cleanup_module.maas_client, "_kube", lambda: None)
    monkeypatch.setattr(cleanup_module.maas_client, "sa_token", lambda: "test-token")
    monkeypatch.setattr(cleanup_module.maas_client, "maas_api_url", lambda: "https://maas.example.com")

    run_id = "manual-cleanup-002"
    await _init_run_row(run_id)
    (tmp_path / f"{run_id}-config.json").write_text(
        json.dumps({"tasks": [{"name": "_cleanup_broken", "params": {}}]})
    )

    try:
        await cleanup_module.run_manual_cleanup(run_id)

        async with aiosqlite.connect(get_db_path()) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT cleanup_status FROM runs WHERE id=?", (run_id,)) as cur:
                row = await cur.fetchone()
        assert row is not None
        assert row["cleanup_status"] == "failed"
    finally:
        REGISTRY.pop("_cleanup_broken", None)


async def test_run_manual_cleanup_skips_unknown_task_name(tmp_path, monkeypatch) -> None:
    """A scenario since edited/removed shouldn't crash a manual cleanup — the
    known tasks in the list still get their cleanup() called."""
    calls: list[str] = []

    class _Known(Task):
        async def run(self, ctx: TaskContext):  # type: ignore[override]  # pragma: no cover - not exercised
            raise NotImplementedError

        async def cleanup(self, ctx: TaskContext) -> None:
            calls.append(self.name)

    REGISTRY["_cleanup_known"] = _Known
    monkeypatch.setattr(cleanup_module, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cleanup_module.maas_client, "_kube", lambda: None)
    monkeypatch.setattr(cleanup_module.maas_client, "sa_token", lambda: "test-token")
    monkeypatch.setattr(cleanup_module.maas_client, "maas_api_url", lambda: "https://maas.example.com")

    run_id = "manual-cleanup-003"
    await _init_run_row(run_id)
    (tmp_path / f"{run_id}-config.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"name": "_cleanup_known", "params": {}},
                    {"name": "_no_longer_registered", "params": {}},
                ]
            }
        )
    )

    try:
        await cleanup_module.run_manual_cleanup(run_id)
        assert calls == ["_cleanup_known"]

        async with aiosqlite.connect(get_db_path()) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT cleanup_status FROM runs WHERE id=?", (run_id,)) as cur:
                row = await cur.fetchone()
        assert row is not None
        assert row["cleanup_status"] == "done"
    finally:
        REGISTRY.pop("_cleanup_known", None)


async def test_run_manual_cleanup_updates_each_resources_status_on_the_run_page(tmp_path, monkeypatch) -> None:
    """Resources left in place by a run with auto cleanup off flip to
    "removed" on the run page once Clean Up Now succeeds for their task."""

    class _Ok(Task):
        async def run(self, ctx: TaskContext):  # type: ignore[override]  # pragma: no cover
            raise NotImplementedError

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_cleanup_ok"] = _Ok
    monkeypatch.setattr(cleanup_module, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cleanup_module.maas_client, "_kube", lambda: None)
    monkeypatch.setattr(cleanup_module.maas_client, "sa_token", lambda: "test-token")
    monkeypatch.setattr(cleanup_module.maas_client, "maas_api_url", lambda: "https://maas.example.com")

    run_id = "manual-cleanup-res"
    await _init_run_row(run_id)
    (tmp_path / f"{run_id}-config.json").write_text(json.dumps({"tasks": [{"name": "_cleanup_ok"}]}))
    created = [{"kind": "API key", "name": "k", "task": "_cleanup_ok", "action": "created", "status": "left in place"}]
    (tmp_path / f"{run_id}-cleanup-state.json").write_text(json.dumps({"_created": created}))
    (tmp_path / f"{run_id}-progress.json").write_text(json.dumps({"tasks": [], "resources": created}))

    try:
        await cleanup_module.run_manual_cleanup(run_id)
    finally:
        REGISTRY.pop("_cleanup_ok", None)

    progress = json.loads((tmp_path / f"{run_id}-progress.json").read_text())
    assert progress["resources"][0]["status"] == "removed"
