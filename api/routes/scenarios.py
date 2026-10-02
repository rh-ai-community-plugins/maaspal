import os
from pathlib import Path

import yaml
from fastapi import APIRouter

router = APIRouter()

# Display/launch-form metadata a scenario YAML may declare (ADR-025). The
# harness never reads these — only this route and the UI do — so they can
# evolve without touching harness/config.py.
_LIST_FIELDS = ("mutates", "requires", "needs_rbac", "previous_names")


def _scenarios_dir() -> Path:
    return Path(os.environ.get("SCENARIOS_DIR", "scenarios"))


def _title_from_name(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("_"))


@router.get("/api/scenarios")
async def list_scenarios() -> list[dict]:
    result = []
    for f in sorted(_scenarios_dir().glob("*.yaml")):
        if f.stem.startswith("stub") or f.stem.startswith("_"):
            continue
        raw = yaml.safe_load(f.read_text())
        name = raw.get("name", f.stem)
        result.append(
            {
                "name": name,
                "title": raw.get("title") or _title_from_name(name),
                "summary": raw.get("summary") or "",
                "description": raw.get("description", ""),
                "config": raw.get("config", {}),
                # A scenario with no explicit category (e.g. one someone
                # writes themselves) lands in "Custom" automatically — the
                # UI always shows that bucket, so this is the only default
                # needed to make it "just work".
                "category": raw.get("category") or "Custom",
                # "verify": checks the cluster's existing setup (only creates
                # API keys). "explore": creates temporary models/subscriptions/
                # identities to probe how MaaS itself behaves.
                "kind": raw.get("kind") or "verify",
                "est_duration": raw.get("est_duration") or "",
                # Position within its category (lower first); unordered ones follow.
                "order": raw.get("order", 100),
                "inputs": raw.get("inputs") or {},
                "plan_template": raw.get("plan_template") or "",
                **{field: list(raw.get(field) or []) for field in _LIST_FIELDS},
            }
        )
    return result
