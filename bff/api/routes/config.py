import json
import os
from pathlib import Path

import yaml
from fastapi import APIRouter

router = APIRouter()

_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))


@router.get("/api/runs/{run_id}/config")
async def get_run_config(run_id: str) -> dict:
    """Serve a scenario-YAML-shaped snapshot of this run — name/description/
    config (global settings + scenario config merged, redacted)/tasks/
    assertions/cleanup — as YAML text meant to be pasted directly into a new
    scenarios/*.yaml file to reproduce the run (see harness/runner.py's write of
    {run_id}-config.json, built by _scenario_settings_snapshot).

    Written once, before the run's task loop starts, so this is available from
    the moment the run begins rather than only once it finishes. sort_keys=False
    preserves the snapshot's own key order (name, description, config, ...) so
    it reads like an actual scenario file rather than an alphabetized dump.
    """
    path = _DATA_DIR / "results" / f"{run_id}-config.json"
    if not path.exists():
        return {"config_yaml": None}
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        return {"config_yaml": yaml.safe_dump(snapshot, sort_keys=False, default_flow_style=False)}
    except Exception:
        return {"config_yaml": None}
