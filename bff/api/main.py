import asyncio
import json
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from fastapi import Depends, FastAPI

from api.auth import require_user
from api.db import get_db_path, init_db
from api.k8s import delete_stopped_job, run_job_state
from api.routes.assertions import router as assertions_router
from api.routes.browser import router as browser_router
from api.routes.config import router as config_router
from api.routes.logs import router as logs_router
from api.routes.maas import router as maas_router
from api.routes.progress import router as progress_router
from api.routes.runs import router as runs_router
from api.routes.scenarios import router as scenarios_router
from harness.cleanup_state import read_cleanup_status

_RESULTS_DIR = Path("/data/results")
_POLL_INTERVAL_S = 10

# Runs seen once with a dead harness (Job suspended/failed, no pod, no result
# file) — finalized only when still true on the next poll, so a result file
# written just as the pod exits isn't raced.
_dead_harness_seen: set[str] = set()


def _finalize_without_result(run_id: str) -> tuple[str, str | None] | None:
    """Safety net for a harness that died without writing its result (SIGKILL
    after an overrun grace period, OOMKill): the run would otherwise stay
    RUNNING forever. Returns (status, note) once it's certain, else None."""
    try:
        state, pod_exists = run_job_state(run_id)
    except Exception as exc:
        print(f"[api] could not read Job state for {run_id}: {exc}", flush=True)
        return None
    if pod_exists or state not in ("suspended", "failed"):
        _dead_harness_seen.discard(run_id)
        return None
    if run_id not in _dead_harness_seen:
        _dead_harness_seen.add(run_id)
        return None
    _dead_harness_seen.discard(run_id)
    status = "CANCELLED" if state == "suspended" else "FAIL"
    return status, "The run's process ended without reporting a result."


async def _sync_completed_runs() -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM runs WHERE status='RUNNING'") as cur:
            running = [row["id"] for row in await cur.fetchall()]

    for run_id in running:
        result_path = _RESULTS_DIR / f"{run_id}.json"
        if not result_path.exists():
            finalized = await asyncio.to_thread(_finalize_without_result, run_id)
            if finalized is None:
                continue
            status, note = finalized
            cleanup_info = read_cleanup_status(_RESULTS_DIR, run_id)
            async with aiosqlite.connect(get_db_path()) as db:
                await db.execute(
                    "UPDATE runs SET status=?, updated_at=?, cleanup_status=?, cleanup_error=? WHERE id=?",
                    (
                        status,
                        datetime.now(UTC).isoformat(),
                        cleanup_info.get("status", "pending"),
                        cleanup_info.get("error"),
                        run_id,
                    ),
                )
                await db.commit()
            print(f"[api] run {run_id} → {status} (no result written: {note})", flush=True)
            if status == "CANCELLED":
                await asyncio.to_thread(delete_stopped_job, run_id)
            continue
        try:
            data = json.loads(result_path.read_text())
            status = data.get("status", "FAIL")
            duration_ms = data.get("duration_ms")
        except Exception:
            print(
                f"[api] could not read result for {run_id}\n{traceback.format_exc()}",
                flush=True,
            )
            status = "FAIL"
            duration_ms = None

        # By the time {run_id}.json exists, harness/runner.py has already made
        # its cleanup decision and written {run_id}-cleanup-status.json — no
        # separate poll cadence needed, this file is guaranteed final here. A
        # run whose harness predates this feature (no status file ever
        # written) defaults to "done", matching that era's unconditional
        # cleanup rather than showing a stale "Clean Up Now" prompt.
        cleanup_info = read_cleanup_status(_RESULTS_DIR, run_id)
        cleanup_status = cleanup_info.get("status", "pending")
        if not result_path.with_name(f"{run_id}-cleanup-status.json").exists():
            cleanup_status = "done"
        cleanup_error = cleanup_info.get("error")

        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                "UPDATE runs SET status=?, updated_at=?, duration_ms=?, cleanup_status=?, cleanup_error=? "
                "WHERE id=?",
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    duration_ms,
                    cleanup_status,
                    cleanup_error,
                    run_id,
                ),
            )
            await db.commit()
        print(f"[api] run {run_id} → {status} (cleanup: {cleanup_status})", flush=True)
        if status == "CANCELLED":
            await asyncio.to_thread(delete_stopped_job, run_id)


async def _poll_job_statuses() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL_S)
        try:
            await _sync_completed_runs()
        except Exception:
            print(f"[api] status poll error\n{traceback.format_exc()}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    task = asyncio.create_task(_poll_job_statuses())
    yield
    task.cancel()


# Every route is gated on the dashboard user's forwarded token (api/auth.py,
# ADR-026). The UI itself is a Module Federation remote served by its own nginx
# container, so this app serves only the API.
app = FastAPI(
    title="MaaS:PAL", version="0.1.0", lifespan=lifespan, dependencies=[Depends(require_user)]
)
app.include_router(scenarios_router)
app.include_router(runs_router)
app.include_router(logs_router)
app.include_router(assertions_router)
app.include_router(progress_router)
app.include_router(config_router)
app.include_router(browser_router)
app.include_router(maas_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
