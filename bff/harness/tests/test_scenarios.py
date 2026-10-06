import pathlib
import re

import pytest
import yaml

from api.routes.scenarios import BUILTIN_SCENARIOS, CUSTOM_CATEGORY, KNOWN_CATEGORIES
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
# The API's category list (mirrored by src/app/components/ScenarioCatalog.tsx).
# "Custom" is the API's own default for any scenario with no known `category:`.
_KNOWN_CATEGORIES = {*KNOWN_CATEGORIES, CUSTOM_CATEGORY}
# Display/launch metadata every built-in scenario declares (ADR-025) — a
# custom scenario may omit them all (the API falls back to sane defaults).
_REQUIRED_METADATA = {"title", "summary", "kind"}
_KNOWN_KINDS = {"verify", "explore"}
_KNOWN_MUTATES = {"api_keys", "subscriptions", "auth_policies", "models", "service_accounts", "routes"}


def test_exactly_sixteen_production_scenarios() -> None:
    assert len(_SCENARIO_PATHS) == 16, (
        f"Expected 16 scenario files, found {len(_SCENARIO_PATHS)}: "
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
        "— update both this test and src/app/components/ScenarioCatalog.tsx together"
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


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=[p.stem for p in _SCENARIO_PATHS])
def test_send_requests_steps_follow_request_api_settings(path: pathlib.Path) -> None:
    """Every scenario that sends inference requests offers the Request API and
    Streaming settings as advanced inputs, and every such step uses them —
    checked by task class, so a new send_requests alias is covered too."""
    from harness.tasks.inference import API_PATHS, SendRequestsTask
    from harness.tasks.subscription_check import SendRequestsToEachModelTask

    raw = yaml.safe_load(path.read_text())
    steps = [
        t for t in raw.get("tasks") or []
        if issubclass(REGISTRY[t["name"]], (SendRequestsTask, SendRequestsToEachModelTask))
    ]
    if not steps:
        return
    config, inputs = raw.get("config") or {}, raw.get("inputs") or {}
    assert config.get("request_api") == "chat_completions", f"{path.name}: request_api must default to chat"
    assert config.get("stream") is False, f"{path.name}: stream must default to false"
    assert inputs.get("request_api", {}).get("advanced"), f"{path.name}: request_api must be an advanced input"
    assert inputs.get("stream", {}).get("advanced"), f"{path.name}: stream must be an advanced input"
    assert set(inputs["request_api"].get("choices") or []) == set(API_PATHS), path.name
    for step in steps:
        params = step.get("params") or {}
        assert params.get("api") == "${config.request_api}", f"{path.name}: {step['name']} ignores request_api"
        assert params.get("stream") == "${config.stream}", f"{path.name}: {step['name']} ignores stream"


# Scenarios whose requests must come from inside the cluster: both legs of a
# direct-vs-MaaS comparison go to in-cluster endpoints a browser can't reach.
_NO_BROWSER = {"gateway_overhead", "request_types"}


@pytest.mark.parametrize("path", _SCENARIO_PATHS, ids=lambda p: p.stem)
def test_send_requests_steps_offer_send_from_browser(path: pathlib.Path) -> None:
    """Every scenario that sends inference requests offers "Send from user
    browser" as an advanced checkbox, and every such step follows it. Large
    sends (until throttled, step load) default to sending from the pod."""
    from harness.tasks.inference import SendRequestsTask
    from harness.tasks.subscription_check import SendRequestsToEachModelTask

    raw = yaml.safe_load(path.read_text())
    steps = [
        t for t in raw.get("tasks") or []
        if issubclass(REGISTRY[t["name"]], (SendRequestsTask, SendRequestsToEachModelTask))
    ]
    config, inputs = raw.get("config") or {}, raw.get("inputs") or {}
    if not steps or raw["name"] in _NO_BROWSER:
        assert "send_from_browser" not in config, f"{path.name}: can't send from a browser"
        return
    assert isinstance(config.get("send_from_browser"), bool), f"{path.name}: send_from_browser must be a boolean"
    assert inputs.get("send_from_browser", {}).get("advanced"), f"{path.name}: send_from_browser must be advanced"
    large = any((s.get("params") or {}).get("until_throttled") or (s.get("params") or {}).get("stages") for s in steps)
    if large:
        assert config["send_from_browser"] is False, f"{path.name}: large sends default to the pod"
    for step in steps:
        params = step.get("params") or {}
        assert params.get("from_browser") == "${config.send_from_browser}", (
            f"{path.name}: {step['name']} ignores send_from_browser"
        )


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


def test_every_builtin_scenario_has_a_file() -> None:
    """BUILTIN_SCENARIOS decides what the catalog labels "Custom" — a renamed
    or removed built-in must be updated there too. (A subset check, so extra
    scenario files someone adds never break this.)"""
    names = {yaml.safe_load(p.read_text()).get("name", p.stem) for p in _SCENARIO_PATHS}
    assert names >= BUILTIN_SCENARIOS, sorted(BUILTIN_SCENARIOS - names)
