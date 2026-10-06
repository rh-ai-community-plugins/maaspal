"""Hand a "Send from user browser" step to the open run page, and take back
what it sent (send_requests `from_browser`; files in harness/browser_channel.py).

The harness Job stays the source of truth: it writes an order, this hands it
to exactly one browser tab (the claim), and appends the raw per-request
records the tab reports. The Job tallies them through the same code as
requests it sends itself, so checks, charts and logs are unchanged.

The order carries the run's own temporary API keys (revoked at cleanup) —
the claim response is the only place a key value leaves the cluster."""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from api.db import get_db_path
from harness import browser_channel

router = APIRouter()

# Response headers a browser may report — the ones failure_origin() reads.
_KEPT_HEADERS = {"server", "x-ext-auth-reason"}


def _results_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "results"


async def _require_running(run_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status FROM runs WHERE id=?", (run_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if row["status"] != "RUNNING":
        raise HTTPException(status_code=409, detail=f"Run is {row['status']}")


class ClaimRequest(BaseModel):
    origin: str = Field("", max_length=300)


class BrowserAttempt(BaseModel):
    status: int | None = Field(None, ge=100, le=599)
    reason: str = ""
    ms: float = Field(0.0, ge=0)


class BrowserRecord(BaseModel):
    t0_offset_s: float = Field(ge=0, le=86_400)
    latency_ms: float = Field(ge=0, le=86_400_000)
    ok: bool = False
    status: int | None = Field(None, ge=100, le=599)
    # Response text and browser errors are kept exactly as received —
    # no length caps, so nothing is ever cut short.
    reason: str = ""
    error: str | None = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=50)
    body: str = ""
    usage: dict[str, int] | None = Field(None, max_length=10)
    arrivals: list[float] = Field(default_factory=list, max_length=20_000)
    attempts: list[BrowserAttempt] = Field(default_factory=list, max_length=20)


class BrowserResults(BaseModel):
    claim_id: str = Field(max_length=64)
    result_key: str = Field(max_length=200)
    records: list[BrowserRecord] = Field(default_factory=list, max_length=500)
    done: bool = False
    reason: Literal["finished", "blocked", "stopped", "error"] = "finished"
    detail: str = ""


@router.get("/api/runs/{run_id}/browser-work")
async def get_browser_work(run_id: str, response: Response) -> dict:
    """Whether a step is waiting for a browser — no keys, safe to poll."""
    response.headers["Cache-Control"] = "no-store"
    found = browser_channel.find_order(_results_dir(), run_id)
    if found is None:
        return {"pending": False}
    result_key, order = found
    claimed = browser_channel.read_claim(_results_dir(), run_id, result_key) is not None
    return {
        "pending": True,
        "claimed": claimed,
        "step": order.get("step"),
        "result_key": result_key,
        "count": len(order.get("plan") or []),
    }


@router.post("/api/runs/{run_id}/browser-work/claim")
async def claim_browser_work(run_id: str, body: ClaimRequest, response: Response) -> dict:
    """Take the waiting step for this tab: the order, keys included."""
    response.headers["Cache-Control"] = "no-store"
    await _require_running(run_id)
    found = browser_channel.find_order(_results_dir(), run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No step is waiting for a browser")
    result_key, order = found
    claim_id = uuid.uuid4().hex
    claim = {
        "claim_id": claim_id,
        "order_id": order.get("order_id"),
        "origin": body.origin,
        "claimed_at": datetime.now(UTC).isoformat(),
    }
    if not browser_channel.try_claim(_results_dir(), run_id, result_key, claim):
        raise HTTPException(status_code=409, detail="Another browser tab is already sending this step")
    return {**order, "result_key": result_key, "claim_id": claim_id}


@router.post("/api/runs/{run_id}/browser-results", status_code=204)
async def post_browser_results(run_id: str, body: BrowserResults) -> Response:
    """Append what the claiming tab sent. Raw outcomes only — the harness
    computes every number from them."""
    await _require_running(run_id)
    claim = browser_channel.read_claim(_results_dir(), run_id, body.result_key)
    if claim is None or claim.get("claim_id") != body.claim_id:
        # The step ended (or was never this tab's) — stop sending.
        raise HTTPException(status_code=410, detail="This step is no longer being sent from this tab")
    lines = []
    for rec in body.records:
        line = rec.model_dump()
        line["headers"] = {k.lower(): v for k, v in rec.headers.items() if k.lower() in _KEPT_HEADERS}
        lines.append(line)
    if body.done:
        lines.append({"done": True, "reason": body.reason, "detail": body.detail})
    browser_channel.append_records(_results_dir(), run_id, body.result_key, lines)
    return Response(status_code=204)
