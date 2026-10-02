import asyncio

from fastapi import APIRouter

from api.k8s import ensure_log_capture, get_log_lines

router = APIRouter()


@router.get("/api/runs/{run_id}/logs/lines")
async def get_run_log_lines(run_id: str, offset: int = 0) -> dict:
    """Poll-based log endpoint.

    Returns log lines starting at *offset* (line number, 0-indexed) and
    whether the run has finished writing.  Call repeatedly with the returned
    ``next_offset`` until ``done`` is true.

    This endpoint is deliberately plain JSON — no SSE, no long-lived
    connection — so it works reliably through HAProxy, the dashboard's proxy, the webpack dev proxy, or any
    other HTTP proxy without buffering issues.
    """
    # Ensure a background thread is capturing pod logs to disk.
    ensure_log_capture(run_id)

    lines, done = await asyncio.to_thread(get_log_lines, run_id, offset)
    return {
        "lines": lines,
        "done": done,
        "next_offset": offset + len(lines),
    }
