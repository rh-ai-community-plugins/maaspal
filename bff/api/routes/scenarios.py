import os
from pathlib import Path

import yaml
from fastapi import APIRouter

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


def _scenarios_dir() -> Path:
    return Path(os.environ.get("SCENARIOS_DIR", "scenarios"))


def _title_from_name(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("_"))


@router.get("/api/scenarios")
async def list_scenarios() -> list[dict]:
    result = []
    env = cluster_defaults()
    for f in sorted(_scenarios_dir().glob("*.yaml")):
        if f.stem.startswith("stub") or f.stem.startswith("_"):
            continue
        raw = yaml.safe_load(f.read_text())
        name = raw.get("name", f.stem)
        category = raw.get("category")
        result.append(
            {
                "name": name,
                "title": raw.get("title") or _title_from_name(name),
                "summary": raw.get("summary") or "",
                "description": raw.get("description", ""),
                "config": resolve_config_defaults(raw.get("config", {}) or {}, env),
                # A scenario may join a built-in category; with no category,
                # or one the UI doesn't know, it lands in "Custom".
                "category": category if category in KNOWN_CATEGORIES else CUSTOM_CATEGORY,
                "custom": name not in BUILTIN_SCENARIOS,
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
