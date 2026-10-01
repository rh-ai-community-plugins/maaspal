import asyncio
import json
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.db import get_db_path, init_db
from api.k8s import delete_stopped_job
from api.routes.assertions import router as assertions_router
from api.routes.config import router as config_router
from api.routes.logs import router as logs_router
from api.routes.maas import router as maas_router
from api.routes.progress import router as progress_router
from api.routes.runs import router as runs_router
from api.routes.scenarios import router as scenarios_router
from harness.cleanup_state import read_cleanup_status

_RESULTS_DIR = Path("/data/results")
_POLL_INTERVAL_S = 10


async def _sync_completed_runs() -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM runs WHERE status='RUNNING'") as cur:
            running = [row["id"] for row in await cur.fetchall()]

    for run_id in running:
        result_path = _RESULTS_DIR / f"{run_id}.json"
        if not result_path.exists():
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
                    datetime.now(timezone.utc).isoformat(),
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


app = FastAPI(title="MaaS:PAL", version="0.1.0", lifespan=lifespan)
app.include_router(scenarios_router)
app.include_router(runs_router)
app.include_router(logs_router)
app.include_router(assertions_router)
app.include_router(progress_router)
app.include_router(config_router)
app.include_router(maas_router)

_ui_dist = Path(__file__).parent.parent / "ui" / "dist"
if _ui_dist.exists():
    app.mount("/", StaticFiles(directory=str(_ui_dist), html=True), name="ui")
