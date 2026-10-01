import asyncio
import contextlib
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_RESULTS_DIR = _DATA_DIR / "results"


def _read_sa_token(config: dict) -> str:
    if token := config.get("SA_TOKEN", ""):
        return token
    try:
        return Path(_SA_TOKEN_PATH).read_text().strip()
    except OSError:
        return ""


_SENSITIVE_CONFIG_KEY_RE = re.compile(r"(?:^|_)(token|secret|password)(?:$|_)", re.IGNORECASE)
# Deliberately excludes "key" as a generic substring: this app's whole domain is
# provisioning MaaS API *keys*, so plain substring matching false-positived hard
# on entirely non-sensitive fields like key_name/key_pool/total_tokens/
# maas_tokens_match (confirmed live — the last one nuked a whole assertion, not
# just a leaf value, since the check runs on every dict key at every depth). No
# resolved config/task-param field in this codebase actually carries raw key
# material anyway — created key values live only in shared_state at runtime,
# never in scenario config, so they never reach this snapshot in the first place.
#
# "token" can't get the same blanket-substring-exclusion treatment, though —
# unlike "key", real bearer credentials in this codebase ARE named with "token"
# as their own complete word (target_token, sa_token), so the regex still needs
# to catch those. token_limit/token_window (the rate-limit scenarios' MaaS
# subscription config — an LLM token budget and a time window, not a
# credential) are a narrower false positive of the same shape: "token" happens
# to be a complete underscore-delimited word there too. Exact-name carve-out
# instead of a pattern change, so it can't accidentally un-redact anything else.
_SENSITIVE_CONFIG_KEY_EXCEPTIONS = {"token_limit", "token_window"}

# Cluster-level settings worth showing alongside a scenario's own config even
# though they come from the global ConfigMap rather than the scenario YAML.
_GLOBAL_CONFIG_KEYS = ("MAAS_API_URL", "MAAS_METRICS_URL", "DEFAULT_MODEL", "DEFAULT_SUBSCRIPTION")


def _merged_config_block(scenario: dict, resolved_config: dict) -> dict:
    """The effective config: block for this run — the scenario's own declared
    config: keys (defaults with any launch-time overrides actually applied),
    merged with a small curated set of cluster-level settings the scenario
    doesn't declare itself but that affect what it actually talked to.
    _resolved_config is a merge of the *entire* process environment
    (harness/config.py:load_scenario's global_config = dict(os.environ)),
    so this deliberately keeps only these two groups rather than everything —
    container plumbing (PATH, HOSTNAME, KUBERNETES_*, PYTHON_*, ...) was never
    anyone's "setting". Sorted so the rendered YAML reads consistently.
    """
    keys = list((scenario.get("config") or {}).keys())
    keys += [k for k in _GLOBAL_CONFIG_KEYS if k in resolved_config]
    return {k: resolved_config[k] for k in sorted(keys) if k in resolved_config}


def _scenario_settings_snapshot(scenario: dict, resolved_config: dict) -> dict:
    """A scenario-YAML-shaped snapshot of this run — meant to be pasted directly
    into a new scenarios/*.yaml file to reproduce it exactly, not just inspected.
    Reuses the scenario's own tasks/assertions/metrics_queries/cleanup verbatim
    (already ${config.x}-resolved by harness/config.py:load_scenario, and
    unaffected by config overrides) and replaces config: with this run's actual
    merged values (see _merged_config_block).
    """
    snapshot: dict = {
        "name": scenario.get("name"),
        "description": scenario.get("description"),
        "config": _merged_config_block(scenario, resolved_config),
    }
    if scenario.get("metrics_queries"):
        snapshot["metrics_queries"] = scenario["metrics_queries"]
    snapshot["tasks"] = scenario.get("tasks") or []
    if scenario.get("assertions"):
        snapshot["assertions"] = scenario["assertions"]
    snapshot["cleanup"] = scenario.get("cleanup", "automatic")
    return snapshot


def _redact_sensitive_config(value):
    """Recursively mask likely-sensitive values before a run's snapshot is
    written where a user can view it (GET /api/runs/{id}/config) — a dict key
    matching token|secret|password at *any* nesting depth is masked (see
    _SENSITIVE_CONFIG_KEY_EXCEPTIONS for named carve-outs), since a resolved
    ${config.target_token}-style value can end up inside a task's params or an
    assertion, not just the top-level config: block.
    """
    if isinstance(value, dict):
        return {
            k: (
                "***REDACTED***"
                if k not in _SENSITIVE_CONFIG_KEY_EXCEPTIONS and _SENSITIVE_CONFIG_KEY_RE.search(k)
                else _redact_sensitive_config(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_config(v) for v in value]
    return value

from harness.cleanup_state import (
    mark_left_in_place,
    mark_task_cleanup,
    resources_from_state,
    read_auto_cleanup_flag,
    write_cleanup_state,
    write_cleanup_status,
)
from harness.config import load_scenario
from harness.metrics_client import fetch_metrics
from harness.result import (
    AssertionResult,
    RunResult,
    TaskResult,
    compute_run_status,
    evaluate_all_assertions,
)
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

_EMIT_DEBOUNCE_S = 0.1
# Default cap for settling a metrics-dependent assertion (see _settle_and_evaluate).
# Confirmed on the cluster this repo targets: Prometheus/Thanos scrapeInterval is 30s
# cluster-wide (no per-target override on the Limitador PodMonitor), so a request
# landing right after a scrape must wait nearly the full 30s for the next one — 65s
# leaves real headroom above that for scrape jitter/an occasional delayed scrape under
# load. Override per-scenario via an assertion's `max_wait_s` (see _configured_max_wait_s
# below) if a cluster's interval differs or needs more headroom.
#
# Earlier designs tried to detect "has a fresh scrape landed" indirectly — comparing
# fetched values against a baseline, against the previous poll, against the Prometheus
# response's own timestamp — and each broke on a different, increasingly subtle
# Prometheus API behavior (see ADR-014 for the full history). This settles that by
# checking the only thing that actually matters directly: does the assertion pass yet.
_METRICS_FINAL_MAX_WAIT_S = 65.0
_METRICS_FINAL_POLL_INTERVAL_S = 5.0


def _configured_max_wait_s(assertions: dict, task_defs: list[dict]) -> float:
    """Largest `max_wait_s` set on any match-form assertion (top-level or per-task).

    All match-form assertions share the one background metrics fetch/retry loop, so
    a single scenario-wide value is used — take the max across whatever's configured,
    falling back to the module default when nothing overrides it.
    """
    values = [
        float(spec["max_wait_s"])
        for spec in assertions.values()
        if isinstance(spec, dict) and "max_wait_s" in spec
    ]
    for task_def in task_defs:
        values.extend(
            float(spec["max_wait_s"])
            for spec in (task_def.get("assertions") or {}).values()
            if isinstance(spec, dict) and "max_wait_s" in spec
        )
    return max(values) if values else _METRICS_FINAL_MAX_WAIT_S


# Template variables a promql-form assertion can reference, in addition to
# ${config.x} (already resolved by harness/config.py at scenario-load time, before
# either regex below ever sees the text):
#   ${baseline.<name>}          — a name from this scenario's metrics_queries, as
#                                  snapshotted before the task loop started
#   ${harness.<namespace>.<key>} — a live shared_state value (e.g.
#                                  inference_results.total_requests)
# See ADR-015 for why these resolve on two different schedules (baseline once,
# harness every poll tick) rather than both being handled by config.py's loader.
_BASELINE_VAR_RE = re.compile(r"\$\{baseline\.([A-Za-z0-9_]+)\}")
_HARNESS_VAR_RE = re.compile(r"\$\{harness\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\}")


def _collect_promql_assertions(assertions: dict, task_defs: list[dict]) -> dict[str, str]:
    """Return {assertion_name: promql_template} for every promql-form assertion,
    scenario-level and per-task, so the runner can poll each as its own ad hoc query
    alongside the scenario's named metrics_queries.
    """
    collected: dict[str, str] = {}
    for name, spec in assertions.items():
        if isinstance(spec, dict) and "promql" in spec:
            collected[name] = spec["promql"]
    for task_def in task_defs:
        for name, spec in (task_def.get("assertions") or {}).items():
            if isinstance(spec, dict) and "promql" in spec:
                collected[name] = spec["promql"]
    return collected


def _substitute_baseline_vars(
    template: str, baseline: dict[str, float], declared: set[str]
) -> str | None:
    """Resolve ${baseline.<name>} references against the pre-run metrics snapshot.

    Runs once, right after the baseline fetch — unlike ${harness.*}, baseline values
    never change during a run, so there's no need to re-resolve them on every tick.

    Two distinct failure modes here, handled differently on purpose:
    - ${baseline.<name>} where <name> isn't declared in this scenario's
      metrics_queries at all is an authoring mistake (a typo, or a forgotten
      metrics_queries entry) — raises immediately, loud, at scenario start, the same
      way a bad ${config.x} reference already does.
    - <name> IS declared, but its baseline fetch came back with no data — e.g. a
      label filter (like limitador_namespace) that doesn't match any series on this
      cluster. That's an expected-to-happen environmental/config condition, not a
      code bug, and must not crash the entire run before a single task executes (as
      it did previously) — every other "metric not populated yet" case in this
      codebase degrades to PENDING instead, so this does too: returns None, telling
      the caller to permanently skip firing this assertion's query (the baseline
      never gets re-fetched), and logs why so it's diagnosable instead of silent.
    """
    unresolved: set[str] = set()

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in declared:
            raise KeyError(
                f"${{baseline.{key}}} referenced but {key!r} is not declared in "
                f"this scenario's metrics_queries: {sorted(declared)}"
            )
        if key not in baseline:
            unresolved.add(key)
            return ""
        return str(float(baseline[key]))

    resolved = _BASELINE_VAR_RE.sub(_sub, template)
    if unresolved:
        print(
            f"[runner] baseline metrics {sorted(unresolved)} returned no data at "
            "scenario start (check the query's label filters against what's "
            "actually live on this cluster, e.g. via /api/v1/series) — any "
            "assertion referencing ${baseline." + next(iter(unresolved)) + "} will "
            "stay PENDING for this run",
            flush=True,
        )
        return None
    return resolved


def _substitute_harness_vars(template: str, shared_state: dict) -> str | None:
    """Resolve ${harness.<namespace>.<key>} references against live shared_state.

    Unlike ${config.x}/${baseline.x} (resolved once), these change continuously
    during a run, so this re-runs on every poll tick right before firing the query.
    Returns None if any referenced value isn't populated yet — tells the caller to
    skip firing this tick's query entirely (the assertion stays PENDING) rather than
    substituting a bogus placeholder value.
    """
    missing = False

    def _sub(m: re.Match) -> str:
        nonlocal missing
        namespace, key = m.group(1), m.group(2)
        ns = shared_state.get(namespace, {})
        if not isinstance(ns, dict) or key not in ns:
            missing = True
            return ""
        return str(float(ns[key]))

    resolved = _HARNESS_VAR_RE.sub(_sub, template)
    return None if missing else resolved


# The traffic chart only needs enough points to draw the shape of a burst —
# keep the progress JSON small however many requests a run sends.
_TIMELINE_MAX_POINTS = 600


def _downsample_timeline(timeline: list[list]) -> list[list]:
    """Thin a send_requests timeline to at most _TIMELINE_MAX_POINTS entries,
    always keeping the first and last points and every point where the
    outcome changes (e.g. the very first 429) so the chart never hides the
    moment throttling started."""
    if len(timeline) <= _TIMELINE_MAX_POINTS:
        return list(timeline)
    stride = len(timeline) / _TIMELINE_MAX_POINTS
    keep = {int(i * stride) for i in range(_TIMELINE_MAX_POINTS)}
    keep.update({0, len(timeline) - 1})
    keep.update(i for i in range(1, len(timeline)) if timeline[i][2] != timeline[i - 1][2])
    return [timeline[i] for i in sorted(keep)]


def _traffic_snapshot(shared_state: dict, default_limit: object = None) -> list[dict]:
    """One entry per send_requests-family invocation (keyed by result_key),
    for the run page's traffic summary card and chart. `limit` draws the
    chart's token-limit reference line: a burst's own (e.g. one model's limit
    in verify_subscription_models), else the scenario's `token_limit` config."""
    out = []
    for result_key, info in (shared_state.get("_traffic") or {}).items():
        limit = info.get("limit", default_limit)
        try:
            limit = float(limit) if limit not in (None, "") else None
        except (TypeError, ValueError):
            limit = None
        out.append(
            {
                "task": info.get("task"),
                "result_key": result_key,
                "label": info.get("label"),
                "limit": limit,
                "planned": info.get("planned"),
                "summary": shared_state.get(result_key) or {},
                "timeline": _downsample_timeline(info.get("timeline") or []),
                "chart": bool(info.get("chart")),
            }
        )
    return out


def _tables_snapshot(shared_state: dict) -> list[dict]:
    """Per-row detail tables a task publishes for the run page (e.g. one row
    per model checked), via shared_state["_tables"][title] = {columns, rows}."""
    return [
        {"title": title, "columns": t.get("columns", []), "rows": t.get("rows", [])}
        for title, t in (shared_state.get("_tables") or {}).items()
    ]


def _format_number(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _render_verdict(
    verdict: dict, status: str, shared_state: dict, assertion_results: list[AssertionResult]
) -> dict:
    """One plain-language sentence for the top of the run page. Uses the
    scenario's own `verdict: {pass: ..., fail: ...}` templates when present
    (${harness.<ns>.<key>} filled from final shared_state, "—" if absent),
    otherwise a generic "N of M checks passed"."""
    passed = sum(1 for a in assertion_results if a.status == "PASSING")
    total = len(assertion_results)
    template = (verdict or {}).get("pass" if status == "PASS" else "fail")
    if status == "CANCELLED":
        text = "Run was stopped before it finished — results below are partial."
    elif shared_state.get("_verdict_text"):
        # A task's own conclusion beats a static template — e.g. a rate-limit
        # burst that found the limit too large to use up ("Inconclusive: …"),
        # or a classification task's finding.
        text = str(shared_state["_verdict_text"])
    elif template:
        def _sub(m: re.Match) -> str:
            ns = shared_state.get(m.group(1), {})
            value = ns.get(m.group(2)) if isinstance(ns, dict) else None
            return _format_number(float(value)) if isinstance(value, (int, float)) else "—"

        text = _HARNESS_VAR_RE.sub(_sub, str(template)).strip()
    elif total:
        text = f"{passed} of {total} checks passed."
    else:
        text = "Run completed — this scenario defines no checks."
    return {"status": status, "text": text, "checks_passed": passed, "checks_total": total}


class ScenarioRunner:
    def __init__(
        self, scenario_path: str, run_id: str, stop_event: asyncio.Event | None = None
    ) -> None:
        self.scenario_path = scenario_path
        self.run_id = run_id
        self._last_emit: float = 0.0
        # Set by harness/main.py's SIGTERM handler when the API server asks this run
        # to stop gracefully (see api/k8s.py:stop_run). Checked between tasks and
        # raced against in-flight awaits so a stop interrupts promptly rather than
        # waiting out whatever's currently in progress, while still falling through
        # to the normal task cleanup loop below instead of skipping it.
        self._stop_event = stop_event

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Like asyncio.sleep, but wakes immediately if a stop is requested."""
        if not self._stop_event:
            await asyncio.sleep(seconds)
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    async def _run_task_or_stop(
        self, task: Task, ctx: TaskContext, start: float
    ) -> tuple[TaskResult, bool]:
        """Run task.run(ctx), racing it against a stop request.

        Returns (result, stopped). If a stop wins the race, the in-flight task.run()
        coroutine is cancelled and awaited here (its CancelledError is expected and
        suppressed), and a CANCELLED TaskResult is returned instead — the caller
        still falls through to the normal cleanup loop exactly like any other exit
        from the task loop, so partial state (e.g. some but not all API keys already
        provisioned) still gets cleaned up.
        """
        if not self._stop_event:
            return await task.run(ctx), False

        run_future = asyncio.ensure_future(task.run(ctx))
        stop_waiter = asyncio.ensure_future(self._stop_event.wait())
        done, _ = await asyncio.wait(
            {run_future, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        if run_future in done:
            stop_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_waiter
            return run_future.result(), False

        run_future.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_future
        duration_ms = (time.monotonic() - start) * 1000
        return (
            TaskResult(
                task_name=task.name,
                status="CANCELLED",
                duration_ms=duration_ms,
                error="run stopped by user",
            ),
            True,
        )

    async def run(self) -> RunResult:
        run_start = time.monotonic()
        run_started_at = datetime.now(timezone.utc).isoformat()
        scenario = load_scenario(self.scenario_path)
        scenario_name: str = scenario["name"]
        config: dict = scenario.get("_resolved_config", {})
        assertions: dict[str, str | dict] = scenario.get("assertions") or {}
        task_defs: list[dict] = scenario.get("tasks") or []
        shared_state: dict = {}
        current_task_started_at: str | None = None

        # Written once, before the task loop starts, so a run's effective settings
        # are viewable from the moment it begins (GET /api/runs/{id}/config) rather
        # than only after it finishes — the resolved config never changes mid-run.
        try:
            _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            snapshot = _redact_sensitive_config(_scenario_settings_snapshot(scenario, config))
            (_RESULTS_DIR / f"{self.run_id}-config.json").write_text(
                json.dumps(snapshot), encoding="utf-8"
            )
        except OSError as exc:
            print(f"[runner] could not write config snapshot: {exc}", flush=True)

        def _assertion_dict(r: AssertionResult, task_name: str | None) -> dict:
            return {
                "task": task_name,
                "name": r.name,
                "status": r.status,
                "value": r.current_value,
                "expected_value": r.expected_value,
                "expression": r.expression,
                "label": r.label,
                "description": r.description,
                "unit": r.unit,
                "target": r.target,
            }

        def _write_assertions(
            completed_task_results: list,
            current_task_name: str | None,
            current_task_assertion_results: list,
            global_assertion_results: list,
        ) -> None:
            """Write current assertion state to the dedicated PVC file."""
            payload = (
                [_assertion_dict(r, tr.task_name) for tr in completed_task_results for r in tr.assertions]
                + [_assertion_dict(r, current_task_name) for r in current_task_assertion_results]
                + [_assertion_dict(r, None) for r in global_assertion_results]
            )
            try:
                _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                (_RESULTS_DIR / f"{self.run_id}-assertions.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            except OSError as exc:
                print(f"[runner] could not write assertions: {exc}", flush=True)

        task_completed_progress: dict[str, dict] = {}
        task_completed_summary: dict[str, str] = {}
        # The traffic chart's limit line: this run's effective token_limit, but
        # only for scenarios that declare one (not a stray env var of that name).
        chart_token_limit = (
            config.get("token_limit") if "token_limit" in (scenario.get("config") or {}) else None
        )
        # Set once the run finishes (see the end of this method).
        verdict_payload: dict | None = None

        def _freeze_task_state(task_name: str) -> None:
            """Move the just-finished task's live progress/narration out of
            shared_state into its frozen per-task snapshot."""
            tp = shared_state.pop("task_progress", None)
            if tp:
                task_completed_progress[task_name] = tp
            summary = shared_state.pop("task_summary", None)
            if summary:
                task_completed_summary[task_name] = summary

        def _write_progress(current_idx: int, completed: list[TaskResult]) -> None:
            """Write current task progress to the dedicated PVC file."""
            task_list = []
            for i, task in enumerate(tasks):
                if i < len(completed):
                    chip_status = "DONE" if completed[i].status == "PASS" else completed[i].status
                    entry: dict = {
                        "name": task.name,
                        "status": chip_status,
                        "duration_ms": completed[i].duration_ms,
                    }
                    cp = task_completed_progress.get(task.name)
                    if cp:
                        entry["progress"] = cp
                    summary = task_completed_summary.get(task.name)
                    if summary:
                        entry["summary"] = summary
                    task_assertions = completed[i].assertions
                    if task_assertions:
                        if any(a.status == "FAILING" for a in task_assertions):
                            entry["assertions_status"] = "FAILING"
                        elif all(a.status == "PASSING" for a in task_assertions):
                            entry["assertions_status"] = "PASSING"
                        else:
                            entry["assertions_status"] = "PENDING"
                    task_list.append(entry)
                elif i == current_idx:
                    entry: dict = {"name": task.name, "status": "RUNNING"}
                    if current_task_started_at:
                        entry["started_at"] = current_task_started_at
                    tp = shared_state.get("task_progress")
                    if tp:
                        entry["progress"] = tp
                    summary = shared_state.get("task_summary")
                    if summary:
                        entry["summary"] = summary
                    task_list.append(entry)
                else:
                    task_list.append({"name": task.name, "status": "PENDING"})
            try:
                _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                payload: dict = {
                    "tasks": task_list,
                    "run_started_at": run_started_at,
                    "traffic": _traffic_snapshot(shared_state, chart_token_limit),
                    "resources": resources_from_state(shared_state),
                    "findings": list(shared_state.get("_findings") or []),
                    "tables": _tables_snapshot(shared_state),
                    "metrics_charts": _metrics_charts_snapshot(),
                }
                if verdict_payload:
                    payload["verdict"] = verdict_payload
                (_RESULTS_DIR / f"{self.run_id}-progress.json").write_text(
                    json.dumps(payload, default=str), encoding="utf-8"
                )
            except OSError as exc:
                print(f"[runner] could not write progress: {exc}", flush=True)

        current_task_assertions: dict[str, str | dict] = {}

        async def emit() -> None:
            now = time.monotonic()
            if now - self._last_emit < _EMIT_DEBOUNCE_S:
                return
            self._last_emit = now
            task_name = tasks[current_task_idx].name if current_task_idx >= 0 else None
            _write_assertions(
                completed_task_results=task_results,
                current_task_name=task_name,
                current_task_assertion_results=evaluate_all_assertions(current_task_assertions, shared_state),
                global_assertion_results=evaluate_all_assertions(assertions, shared_state),
            )
            _write_progress(current_task_idx, task_results)

        sa_token = _read_sa_token(config)
        metrics_url: str = config.get("MAAS_METRICS_URL", "")
        metrics_queries: dict[str, str] = scenario.get("metrics_queries") or {}
        # `metrics_charts:` pairs a MaaS-reported value (a shared_state
        # "metrics" key, e.g. total_tokens_delta) with the harness's own count
        # of the same thing (e.g. inference_results.total_tokens_sent). Both are
        # sampled on every metrics poll — including while settling after the
        # traffic stops — so the run page can show MaaS's counter catching up
        # with what was really sent (Prometheus scrape lag), not just whether
        # the two matched in the end.
        metrics_charts: list[dict] = scenario.get("metrics_charts") or []
        metrics_chart_points: list[list[list]] = [[] for _ in metrics_charts]

        def _record_metrics_chart_points() -> None:
            t = round(time.monotonic() - run_start, 1)
            metrics = shared_state.get("metrics") or {}
            for chart_spec, points in zip(metrics_charts, metrics_chart_points, strict=True):
                ns, _, key = str(chart_spec.get("harness", "")).partition(".")
                harness_value = (shared_state.get(ns) or {}).get(key, 0)
                maas_value = metrics.get(chart_spec.get("maas"))
                if maas_value is None:
                    continue
                points.append([t, float(maas_value), float(harness_value)])

        def _metrics_charts_snapshot() -> list[dict]:
            return [
                {
                    "title": c.get("title", ""),
                    "unit": c.get("unit", ""),
                    "maas_label": c.get("maas_label", "Reported by MaaS"),
                    "harness_label": c.get("harness_label", "Sent by this run"),
                    "points": _downsample_timeline(points),
                }
                for c, points in zip(metrics_charts, metrics_chart_points, strict=True)
                if points
            ]
        # Promql-form assertions (see harness/result.py:_evaluate_promql_assertion)
        # each fire their own ad hoc query, in addition to the named metrics_queries
        # above. promql_after_baseline holds each template with ${baseline.x} already
        # resolved (once, right after the baseline fetch below); ${harness.x} is
        # re-resolved every poll tick in _fetch_metrics_once, since those values
        # change continuously during the run.
        promql_templates: dict[str, str] = _collect_promql_assertions(assertions, task_defs)
        # None here means the template's ${baseline.x} reference is declared but came
        # back with no data (see _substitute_baseline_vars) — permanently un-firable
        # for this run, so the assertion just stays PENDING rather than crashing it.
        promql_after_baseline: dict[str, str | None] = dict(promql_templates)
        metrics_enabled = bool(metrics_url and (metrics_queries or promql_templates))

        async def _fetch_metrics_once() -> None:
            if not metrics_enabled:
                return
            resolved_assertion_queries: dict[str, str] = {}
            for name, tmpl in promql_after_baseline.items():
                if tmpl is None:
                    continue
                resolved = _substitute_harness_vars(tmpl, shared_state)
                if resolved is not None:
                    resolved_assertion_queries[name] = resolved
            all_queries = {**metrics_queries, **resolved_assertion_queries}
            if not all_queries:
                return
            try:
                raw = await fetch_metrics(metrics_url, all_queries, sa_token)
            except Exception as exc:
                print(f"[runner] metrics poll error: {exc}", flush=True)
                return
            if not raw:
                return
            baseline = shared_state.get("metrics_baseline") or {}
            deltas = {
                f"{key}_delta": value - baseline[key]
                for key, value in raw.items()
                if key in baseline
            }
            shared_state["metrics"] = {**raw, **deltas}
            _record_metrics_chart_points()

        async def _settle_and_evaluate(
            assertions_to_check: dict[str, str | dict], max_wait_s: float
        ) -> list[AssertionResult]:
            """Evaluate assertions_to_check, polling MaaS metrics up to max_wait_s and
            re-evaluating after each poll, stopping as soon as every one of them is
            PASSING (or the cap is hit, whichever comes first). Used both right after
            a task with metrics-dependent assertions completes, and for the scenario's
            top-level assertions after cleanup.
            """
            if not metrics_enabled or not assertions_to_check:
                return evaluate_all_assertions(assertions_to_check, shared_state)
            elapsed = 0.0
            while True:
                await _fetch_metrics_once()
                results = evaluate_all_assertions(assertions_to_check, shared_state)
                stopped = bool(self._stop_event and self._stop_event.is_set())
                if all(r.status == "PASSING" for r in results) or elapsed >= max_wait_s or stopped:
                    return results
                await self._interruptible_sleep(_METRICS_FINAL_POLL_INTERVAL_S)
                elapsed += _METRICS_FINAL_POLL_INTERVAL_S

        async def _metrics_bg() -> None:
            while True:
                await _fetch_metrics_once()
                if shared_state.get("metrics"):
                    await emit()
                await self._interruptible_sleep(5)

        ctx = TaskContext(
            run_id=self.run_id,
            scenario_name=scenario_name,
            maas_api_url=config.get("MAAS_API_URL", ""),
            sa_token=sa_token,
            shared_state=shared_state,
            config=config,
            assertions=assertions,
            emit_assertion_state=emit,
            metrics_queries=metrics_queries,
        )

        tasks: list[Task] = []
        for task_def in task_defs:
            task_class = REGISTRY[task_def["name"]]
            tasks.append(task_class(name=task_def["name"], params=task_def.get("params") or {}))

        task_results: list[TaskResult] = []
        current_task_idx = -1
        run_failed = False
        max_wait_s = _configured_max_wait_s(assertions, task_defs)

        _write_progress(-1, [])
        metrics_bg = None
        if metrics_enabled:
            baseline = await fetch_metrics(metrics_url, metrics_queries, sa_token)
            if baseline:
                shared_state["metrics_baseline"] = baseline
            promql_after_baseline = {
                name: _substitute_baseline_vars(tmpl, baseline or {}, set(metrics_queries))
                for name, tmpl in promql_templates.items()
            }
            metrics_bg = asyncio.create_task(_metrics_bg())

        for i, (task, task_def) in enumerate(zip(tasks, task_defs)):
            if self._stop_event and self._stop_event.is_set():
                break
            current_task_idx = i
            current_task_assertions = task_def.get("assertions") or {}
            current_task_started_at = datetime.now(timezone.utc).isoformat()
            _write_progress(i, task_results)
            print(f"[runner] task: {task.name}", flush=True)
            start = time.monotonic()
            try:
                result, stopped = await self._run_task_or_stop(task, ctx, start)
                if stopped:
                    _freeze_task_state(task.name)
                    task_results.append(result)
                    break
                if current_task_assertions:
                    # Settling can take a while (up to max_wait_s) — leave
                    # shared_state["task_progress"] in place until it's done, so the
                    # UI keeps showing the task's last known progress bar throughout
                    # the wait instead of it vanishing the instant task.run() returns
                    # and only reappearing once the task is finally marked DONE below.
                    task_assertion_results = await _settle_and_evaluate(
                        current_task_assertions, max_wait_s
                    )
                    result.assertions = task_assertion_results
                    if result.status == "PASS" and any(a.status == "FAILING" for a in task_assertion_results):
                        result.status = "FAIL"
                        result.error = "assertions failed at task completion"
                _freeze_task_state(task.name)
                task_results.append(result)
                if result.status == "FAIL":
                    run_failed = True
                    break
            except Exception as exc:
                duration_ms = (time.monotonic() - start) * 1000
                print(
                    f"[runner] task FAILED: {task.name}\n{traceback.format_exc()}",
                    flush=True,
                )
                _freeze_task_state(task.name)
                task_results.append(
                    TaskResult(
                        task_name=task.name,
                        status="FAIL",
                        duration_ms=duration_ms,
                        error=str(exc),
                    )
                )
                run_failed = True
                break

        # Persisted unconditionally (regardless of the toggle below) so a later
        # manual "Clean Up Now" — run by the API server, after this process has
        # already exited — has whatever each task's cleanup() needs (created key
        # IDs, original subscription/auth-policy CR bodies, ...). None of that
        # otherwise survives past this process's lifetime.
        write_cleanup_state(_RESULTS_DIR, self.run_id, shared_state)

        if read_auto_cleanup_flag(_RESULTS_DIR, self.run_id):
            write_cleanup_status(_RESULTS_DIR, self.run_id, "cleaning")
            cleanup_failed = False
            for task in reversed(tasks):
                print(f"[runner] cleanup: {task.name}", flush=True)
                try:
                    await task.cleanup(ctx)
                    mark_task_cleanup(shared_state, task.name, ok=True)
                except Exception:
                    cleanup_failed = True
                    mark_task_cleanup(shared_state, task.name, ok=False)
                    print(
                        f"[runner] cleanup FAILED: {task.name}\n{traceback.format_exc()}",
                        flush=True,
                    )
                # Show each resource's removal as it happens.
                _write_progress(len(task_results), task_results)
            write_cleanup_status(_RESULTS_DIR, self.run_id, "failed" if cleanup_failed else "done")
        else:
            print("[runner] auto-cleanup disabled — skipping task cleanup", flush=True)
            mark_left_in_place(shared_state)
            write_cleanup_status(_RESULTS_DIR, self.run_id, "skipped")
        # Persist the per-resource statuses too, so a later manual "Clean Up
        # Now" (api/cleanup.py) starts from them.
        write_cleanup_state(_RESULTS_DIR, self.run_id, shared_state)

        # Stop the background metrics poller, then let the scenario's top-level
        # assertions settle the same way per-task ones do (_settle_and_evaluate).
        if metrics_bg:
            metrics_bg.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await metrics_bg

        assertion_results = await _settle_and_evaluate(assertions, max_wait_s)
        # Final (non-debounced) writes so files reflect the definitive end state.
        # current_task_assertions cleared — all task assertions are now frozen in task_results.
        current_task_assertions = {}
        _write_assertions(
            completed_task_results=task_results,
            current_task_name=None,
            current_task_assertion_results=[],
            global_assertion_results=assertion_results,
        )
        stopped = bool(self._stop_event and self._stop_event.is_set())
        status = (
            "CANCELLED" if stopped
            else "FAIL" if run_failed
            else compute_run_status(task_results, assertion_results)
        )
        all_assertion_results = [a for tr in task_results for a in tr.assertions] + assertion_results
        verdict_payload = _render_verdict(
            scenario.get("verdict") or {}, status, shared_state, all_assertion_results
        )
        _write_progress(len(tasks), task_results)

        return RunResult(
            run_id=self.run_id,
            scenario_name=scenario_name,
            status=status,
            tasks=task_results,
            assertions=assertion_results,
            duration_ms=(time.monotonic() - run_start) * 1000,
        )
