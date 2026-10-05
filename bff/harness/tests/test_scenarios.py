import pathlib
import re

import pytest
import yaml

from harness.config import load_scenario
from harness.tasks.registry import REGISTRY

_SCENARIO_DIR = pathlib.Path("scenarios")
_SCENARIO_PATHS = sorted(
    p for p in _SCENARIO_DIR.glob("*.yaml") if not p.stem.startswith("stub")
)
_REQUIRED_FIELDS = {"name", "description", "tasks", "cleanup"}
# ${baseline.x}/${harness.x} (ADR-015) are deliberately left unresolved by
# load_scenario() — the runner resolves them later, on its own schedule (baseline
# once after the pre-run snapshot, harness on every poll tick) — so they're expected
# to survive here, unlike ${config.x}, which must always be fully resolved by load time.
_ALLOWED_UNRESOLVED_PREFIXES = ("${baseline.", "${harness.")
# Mirrors the fixed display order in src/app/components/ScenarioList.tsx — keeps
# the two lists from drifting apart silently. "Custom" is the API's own
# default for any scenario with no `category:` field (api/routes/scenarios.py),
# so it's deliberately not required on any file here.
_KNOWN_CATEGORIES = {
    "Quick check",
    "Rate limits",
    "Access control",
    "API keys",
    "Usage metrics",
    "Performance",
    "Diagnostics",
    "Custom",
}
# Display/launch metadata every built-in scenario declares (ADR-025) — a
# custom scenario may omit them all (the API falls back to sane defaults).
_REQUIRED_METADATA = {"title", "summary", "kind"}
_KNOWN_KINDS = {"verify", "explore"}
_KNOWN_MUTATES = {"api_keys", "subscriptions", "auth_policies", "models", "service_accounts", "routes"}


def test_exactly_fourteen_production_scenarios() -> None:
    assert len(_SCENARIO_PATHS) == 14, (
        f"Expected 14 scenario files, found {len(_SCENARIO_PATHS)}: "
        f"{[p.name for p in _SCENARIO_PATHS]}"
    )


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_scenario_name_matches_filename(path: pathlib.Path) -> None:
    raw = yaml.safe_load(path.read_text())
    assert raw["name"] == path.stem, (
        f"{path.name}: expected name={path.stem!r}, got {raw['name']!r}"
    )


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_scenario_has_required_fields(path: pathlib.Path) -> None:
    raw = yaml.safe_load(path.read_text())
    missing = _REQUIRED_FIELDS - set(raw)
    assert not missing, f"{path.name}: missing fields {missing}"
    assert raw["cleanup"] == "automatic"


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_scenario_loads_and_resolves(path: pathlib.Path) -> None:
    scenario = load_scenario(str(path))
    # plan_template is UI-only (the launch form substitutes ${config.x} and
    # ${model}/${subscription} live as the user edits) — never harness input.
    scenario.pop("plan_template", None)
    # _resolved_config also carries the whole process environment, which can
    # hold literal ${...} text of its own (e.g. make's MAKEFLAGS) — not scenario
    # content. The scenario's own config is checked through "config".
    scenario.pop("_resolved_config", None)
    resolved_str = str(scenario)
    for token in re.findall(r"\$\{[^}]+\}", resolved_str):
        assert token.startswith(_ALLOWED_UNRESOLVED_PREFIXES), (
            f"{path.name}: unresolved placeholder {token!r} remains after load_scenario"
        )


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_scenario_tasks_all_in_registry(path: pathlib.Path) -> None:
    scenario = load_scenario(str(path))
    for task_def in scenario["tasks"]:
        assert task_def["name"] in REGISTRY, (
            f"{path.name}: task {task_def['name']!r} is not registered"
        )


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_scenario_declares_known_category(path: pathlib.Path) -> None:
    raw = yaml.safe_load(path.read_text())
    category = raw.get("category")
    assert category in _KNOWN_CATEGORIES, (
        f"{path.name}: category {category!r} not in {sorted(_KNOWN_CATEGORIES)} "
        "— update both this test and src/app/components/ScenarioList.tsx together"
    )


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_scenario_declares_display_metadata(path: pathlib.Path) -> None:
    raw = yaml.safe_load(path.read_text())
    missing = _REQUIRED_METADATA - set(raw)
    assert not missing, f"{path.name}: missing display metadata {missing}"
    assert raw["kind"] in _KNOWN_KINDS
    assert set(raw.get("mutates") or []) <= _KNOWN_MUTATES, f"{path.name}: unknown mutates entry"
    # An explore scenario exists to create temporary cluster objects; a verify
    # one must not touch anything beyond its own API keys.
    if raw["kind"] == "verify":
        assert set(raw.get("mutates") or []) <= {"api_keys"}, (
            f"{path.name}: a 'verify' scenario may only create API keys"
        )


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_scenario_ui_metadata_references_real_config_keys(path: pathlib.Path) -> None:
    """inputs/requires/show_if/when all name config keys — a typo there would
    silently do nothing (an input that never renders, a mode that never
    matches), so catch it here instead."""
    raw = yaml.safe_load(path.read_text())
    config_keys = set(raw.get("config") or {})
    inputs = raw.get("inputs") or {}
    assert set(inputs) <= config_keys, f"{path.name}: inputs for unknown config keys {set(inputs) - config_keys}"
    requires = set(raw.get("requires") or [])
    assert requires <= config_keys, f"{path.name}: requires unknown config keys {requires - config_keys}"
    for key, spec in inputs.items():
        for dep, value in (spec.get("show_if") or {}).items():
            assert dep in config_keys, f"{path.name}: {key}.show_if references unknown key {dep!r}"
            dep_choices = (inputs.get(dep) or {}).get("choices")
            if dep_choices:
                assert str(value) in dep_choices, f"{path.name}: {key}.show_if {dep}={value!r} isn't a choice"
        if "choices" in spec:
            assert str(raw["config"][key]) in spec["choices"], (
                f"{path.name}: default for {key!r} isn't one of its choices"
            )
    for task in raw.get("tasks") or []:
        for dep, value in (task.get("when") or {}).items():
            assert dep in config_keys, f"{path.name}: task {task['name']} when: unknown key {dep!r}"
            dep_choices = (inputs.get(dep) or {}).get("choices")
            if dep_choices:
                assert str(value) in dep_choices, f"{path.name}: when {dep}={value!r} isn't a choice"


def test_previous_names_are_unique_and_retired() -> None:
    """previous_names lets old run-history rows resolve to a renamed
    scenario's title — each old id must map to exactly one current scenario,
    and never shadow a scenario that still exists under that id."""
    current = {yaml.safe_load(p.read_text())["name"] for p in _SCENARIO_PATHS}
    seen: dict[str, str] = {}
    for p in _SCENARIO_PATHS:
        raw = yaml.safe_load(p.read_text())
        for old in raw.get("previous_names") or []:
            assert old not in current, f"{p.name}: previous name {old!r} is still a live scenario"
            assert old not in seen, f"{old!r} claimed by both {seen[old]} and {p.name}"
            seen[old] = p.name


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_every_mode_combination_loads(path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each `choices` value of a key some task gates on must still produce a
    loadable scenario with registered tasks."""
    import json

    raw = yaml.safe_load(path.read_text())
    gated = {k for t in raw.get("tasks") or [] for k in (t.get("when") or {})}
    for key in gated:
        for choice in (raw.get("inputs") or {}).get(key, {}).get("choices", []):
            monkeypatch.setenv("MAASPAL_CONFIG_OVERRIDES", json.dumps({key: choice}))
            scenario = load_scenario(str(path))
            assert scenario["tasks"], f"{path.name}: {key}={choice} leaves no tasks"
            for task_def in scenario["tasks"]:
                assert task_def["name"] in REGISTRY
