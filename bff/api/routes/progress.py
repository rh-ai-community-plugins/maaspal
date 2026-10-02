import json
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))


@router.get("/api/runs/{run_id}/progress")
async def get_progress(run_id: str) -> dict:
    path = _DATA_DIR / "results" / f"{run_id}-progress.json"
    if not path.exists():
        return {"tasks": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"tasks": []}
