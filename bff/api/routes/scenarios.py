from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.scenario_store import ScenarioConflict, delete_custom, iter_scenario_files, save_custom
from harness.config import cluster_defaults, resolve_config_defaults

router = APIRouter()

# Display/launch-form metadata a scenario YAML may declare (ADR-025). The
# harness never reads these — only this route and the UI do — so they can
# evolve without touching harness/config.py.
_LIST_FIELDS = ("mutates", "requires", "needs_rbac", "previous_names")

# The scenario categories the UI knows, in display order — mirrored by
# CATEGORY_ORDER in src/app/components/ScenarioCatalog.tsx; keep both in sync.
KNOWN_CATEGORIES = (
    "Quick check",
    "Rate limits",
    "Access control",
    "API keys",
    "Usage metrics",
    "Performance",
    "Diagnostics",
)
CUSTOM_CATEGORY = "Custom"

# The scenarios shipped with MaaS:PAL. Any other scenario file is someone's
# own and is listed as custom: labelled "Custom" in the catalog and shown
# ahead of the built-in ones.
BUILTIN_SCENARIOS = frozenset(
    {
        "api_key_lifecycle",
        "denied_without_auth_policy",
        "feed_pal",
        "gateway_overhead",
        "keys_share_user_budget",
        "load_test",
        "model_config_health",
        "multi_model_load",
        "rate_limit_per_user_or_shared",
        "rate_limit_window_recovery",
        "request_types",
        "smoke_test",
        "subscription_auto_selection",
        "usage_metrics_accuracy",
        "verify_subscription",
        "verify_subscription_rate_limit",
    }
)


def _title_from_name(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("_"))


def _scenario_entry(path: Path, imported: bool, env: dict) -> dict:
    raw = yaml.safe_load(path.read_text())
    name = raw.get("name", path.stem)
    category = raw.get("category")
    return {
        "name": name,
        "title": raw.get("title") or _title_from_name(name),
        "summary": raw.get("summary") or "",
        "description": raw.get("description", ""),
        "config": resolve_config_defaults(raw.get("config", {}) or {}, env),
        # A scenario may join a built-in category; with no category,
        # or one the UI doesn't know, it lands in "Custom".
        "category": category if category in KNOWN_CATEGORIES else CUSTOM_CATEGORY,
        "custom": name not in BUILTIN_SCENARIOS,
        # Imported from the UI (stored on the PVC), so it can be deleted there.
        "imported": imported,
        # "verify": checks the cluster's existing setup (only creates
        # API keys). "explore": creates temporary models/subscriptions/
        # identities to probe how MaaS itself behaves.
        "kind": raw.get("kind") or "verify",
        "est_duration": raw.get("est_duration") or "",
        # Position within its category (lower first); unordered ones follow.
        "order": raw.get("order", 100),
        "inputs": raw.get("inputs") or {},
        "plan_template": raw.get("plan_template") or "",
        # Listed by the UI only once the PAL easter egg is active.
        "easter_egg": bool(raw.get("easter_egg")),
        **{field: list(raw.get(field) or []) for field in _LIST_FIELDS},
    }


@router.get("/api/scenarios")
async def list_scenarios() -> list[dict]:
    env = cluster_defaults()
    return [_scenario_entry(path, imported, env) for path, imported in iter_scenario_files()]


class ImportRequest(BaseModel):
    yaml: str
    replace: bool = False


@router.post("/api/scenarios/import", status_code=201)
async def import_scenario(body: ImportRequest) -> dict:
    try:
        _, path = save_custom(body.yaml, replace=body.replace)
    except ScenarioConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _scenario_entry(path, True, cluster_defaults())


@router.delete("/api/scenarios/{name}", status_code=204)
async def delete_scenario(name: str) -> None:
    if not delete_custom(name):
        raise HTTPException(status_code=404, detail="No imported scenario with that name")
