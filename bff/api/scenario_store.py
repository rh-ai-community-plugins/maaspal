"""Where scenario files live, and importing custom ones.

Built-in scenarios are baked into the image (SCENARIOS_DIR, /app/scenarios).
Scenarios imported from the UI live on the shared PVC (CUSTOM_SCENARIOS_DIR,
default /data/scenarios), which every harness Job mounts too, so a run reads
an imported scenario straight from there. A name in both places resolves to
the built-in file; importing such a name is refused anyway.
"""
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

import yaml

from harness.config import load_scenario
from harness.tasks.registry import REGISTRY

# Imported text is stored verbatim; this only bounds what one request can write.
MAX_SCENARIO_BYTES = 256 * 1024
# The name becomes the file name and part of the Job name.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
_KINDS = {"verify", "explore"}


class ScenarioConflict(Exception):
    """The name is taken: by a built-in scenario, or (without replace) by an
    imported one."""


def builtin_dir() -> Path:
    return Path(os.environ.get("SCENARIOS_DIR", "scenarios"))


def custom_dir() -> Path:
    default = Path(os.environ.get("DATA_DIR", "/data")) / "scenarios"
    return Path(os.environ.get("CUSTOM_SCENARIOS_DIR", str(default)))


def _listed(path: Path) -> bool:
    return not (path.stem.startswith("stub") or path.stem.startswith("_"))


def iter_scenario_files() -> Iterator[tuple[Path, bool]]:
    """Every listed scenario file as (path, imported). A built-in name hides
    an imported file of the same name."""
    builtin = sorted(p for p in builtin_dir().glob("*.yaml") if _listed(p))
    yield from ((p, False) for p in builtin)
    taken = {p.stem for p in builtin}
    for p in sorted(custom_dir().glob("*.yaml")):
        if _listed(p) and p.stem not in taken:
            yield p, True


def scenario_path(name: str) -> Path | None:
    """The file a run of `name` reads, or None if there is no such scenario."""
    if not _NAME_RE.match(name):
        return None
    for directory in (builtin_dir(), custom_dir()):
        path = directory / f"{name}.yaml"
        if path.is_file():
            return path
    return None


def imported_path(name: str) -> Path | None:
    if not _NAME_RE.match(name) or (builtin_dir() / f"{name}.yaml").is_file():
        return None
    path = custom_dir() / f"{name}.yaml"
    return path if path.is_file() else None


def _check_structure(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("The YAML must be a mapping with name, description and tasks.")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("The scenario needs a `name:`.")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"`name: {name}` must be lowercase letters, digits and underscores "
            "(starting with a letter or digit, at most 63 characters)."
        )
    if name.startswith("stub"):
        raise ValueError(f"`name: {name}` can't start with `stub` — those names are hidden from the catalog.")

    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("The scenario needs a non-empty `tasks:` list.")
    for i, task in enumerate(tasks, 1):
        if not isinstance(task, dict) or not isinstance(task.get("name"), str):
            raise ValueError(f"Task {i} needs a `name:`.")
    unknown = sorted({t["name"] for t in tasks if t["name"] not in REGISTRY})
    if unknown:
        raise ValueError(f"Unknown task(s): {', '.join(unknown)}. Known tasks: {', '.join(sorted(REGISTRY))}.")

    kind = raw.get("kind")
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"`kind: {kind}` must be one of: {', '.join(sorted(_KINDS))}.")
    config = raw.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError("`config:` must be a mapping of setting → default value.")
    for field in ("requires", "inputs"):
        refs = raw.get(field) or []
        missing = sorted(str(k) for k in refs if k not in config)
        if missing:
            raise ValueError(f"`{field}:` names settings that aren't in `config:`: {', '.join(missing)}.")
    return raw


def save_custom(text: str, replace: bool = False) -> tuple[str, Path]:
    """Validate a scenario YAML and store it as an imported scenario.

    Raises ValueError for a scenario that can't run, ScenarioConflict when
    the name is taken. Returns (name, stored path)."""
    if len(text.encode()) > MAX_SCENARIO_BYTES:
        raise ValueError(f"The scenario is larger than {MAX_SCENARIO_BYTES // 1024} KB.")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"Not valid YAML: {e}") from e
    name = _check_structure(raw)["name"]

    from api.routes.scenarios import BUILTIN_SCENARIOS

    if name in BUILTIN_SCENARIOS or (builtin_dir() / f"{name}.yaml").is_file():
        raise ScenarioConflict(f"A built-in scenario is already called {name}. Pick another `name:`.")
    directory = custom_dir()
    target = directory / f"{name}.yaml"
    if target.exists() and not replace:
        raise ScenarioConflict(f"An imported scenario is already called {name}.")

    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        # The same load a run does, so `when:` and ${config.x} problems show
        # up now rather than when the Job starts.
        try:
            load_scenario(tmp)
        except Exception as e:
            raise ValueError(f"The scenario doesn't load: {e}") from e
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return name, target


def delete_custom(name: str) -> bool:
    path = imported_path(name)
    if path is None:
        return False
    path.unlink()
    return True
