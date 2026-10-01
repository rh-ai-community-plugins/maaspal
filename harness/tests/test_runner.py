import asyncio
import json
import textwrap
import time
from pathlib import Path

import pytest

from harness.result import TaskResult
from harness.runner import ScenarioRunner
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY


def _write(tmp_path: Path, content: str) -> str:
    f = tmp_path / "scenario.yaml"
    f.write_text(textwrap.dedent(content))
    return str(f)


def test_task_ordering(tmp_path: Path) -> None:
    order: list[str] = []

    class _OrderedTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            order.append(self.name)
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_ordered_a"] = _OrderedTask
    REGISTRY["_ordered_b"] = _OrderedTask

    try:
        path = _write(tmp_path, """
            name: test_order
            config: {}
            tasks:
              - name: _ordered_a
                params: {}
              - name: _ordered_b
                params: {}
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, "order-001").run())
        assert order == ["_ordered_a", "_ordered_b"]
        assert result.status == "PASS"
    finally:
        REGISTRY.pop("_ordered_a", None)
        REGISTRY.pop("_ordered_b", None)


def test_cleanup_runs_on_failure(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path, """
        name: test_cleanup
        config: {}
        tasks:
          - name: stub_pass
            params: {}
          - name: stub_fail
            params: {}
        assertions: {}
    """)
    result = asyncio.run(ScenarioRunner(path, "cleanup-001").run())

    assert result.status == "FAIL"
    out = capsys.readouterr().out
    assert "cleanup" in out


def test_cleanup_runs_in_reverse_order(tmp_path: Path) -> None:
    cleanup_order: list[str] = []

    class _TrackTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            cleanup_order.append(self.name)

    REGISTRY["_track_a"] = _TrackTask
    REGISTRY["_track_b"] = _TrackTask

    try:
        path = _write(tmp_path, """
            name: test_reverse
            config: {}
            tasks:
              - name: _track_a
                params: {}
              - name: _track_b
                params: {}
            assertions: {}
        """)
        asyncio.run(ScenarioRunner(path, "reverse-001").run())
        assert cleanup_order == ["_track_b", "_track_a"]
    finally:
        REGISTRY.pop("_track_a", None)
        REGISTRY.pop("_track_b", None)


def test_cleanup_runs_for_all_tasks_even_on_early_failure(tmp_path: Path) -> None:
    """All tasks receive cleanup calls even if an earlier task failed."""
    cleaned: list[str] = []

    class _EarlyFail(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            raise RuntimeError("fails early")

        async def cleanup(self, ctx: TaskContext) -> None:
            cleaned.append(self.name)

    class _NeverRan(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            cleaned.append(self.name)

    REGISTRY["_early_fail"] = _EarlyFail
    REGISTRY["_never_ran"] = _NeverRan

    try:
        path = _write(tmp_path, """
            name: test_all_cleanup
            config: {}
            tasks:
              - name: _early_fail
                params: {}
              - name: _never_ran
                params: {}
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, "all-cleanup-001").run())
        assert result.status == "FAIL"
        assert "_early_fail" in cleaned
        assert "_never_ran" in cleaned
    finally:
        REGISTRY.pop("_early_fail", None)
        REGISTRY.pop("_never_ran", None)


def test_assertion_state_progression(tmp_path: Path) -> None:
    class _MetricTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state.setdefault("inference_results", {})["error_rate_pct"] = 1.0
            await ctx.emit_assertion_state()
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_metric_task"] = _MetricTask

    try:
        path = _write(tmp_path, """
            name: test_assertions
            config: {}
            tasks:
              - name: _metric_task
                params: {}
            assertions:
              error_rate_pct: "< 5"
        """)
        result = asyncio.run(ScenarioRunner(path, "assert-001").run())
        assert result.status == "PASS"
        assert len(result.assertions) == 1
        assert result.assertions[0].status == "PASSING"
        assert result.assertions[0].current_value == 1.0
    finally:
        REGISTRY.pop("_metric_task", None)


def test_metrics_baseline_delta_feeds_match_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline is snapshotted before tasks run; delta = final - baseline feeds a match assertion."""
    from harness import runner as runner_module

    calls: list[None] = []

    async def fake_fetch_metrics(base_url: str, queries: dict, token: str) -> dict:
        calls.append(None)
        # First call (before the task loop starts) is the baseline; every call
        # after that represents "current" and must stay consistent regardless
        # of how many background/settle polls happen to interleave.
        return {"total_requests": 10.0} if len(calls) == 1 else {"total_requests": 55.0}

    monkeypatch.setattr(runner_module, "fetch_metrics", fake_fetch_metrics)
    # Keep the settle loop fast regardless — the fake above already converges on its
    # first post-baseline call, so a tiny cap/interval doesn't change the outcome,
    # just guards against the loop ever needing a second iteration for some other reason.
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_MAX_WAIT_S", 0.05)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_POLL_INTERVAL_S", 0.01)

    class _ExpectSentCount(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state["inference_results"] = {"total_requests": 45.0}
            await ctx.emit_assertion_state()
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_expect_sent_count"] = _ExpectSentCount

    try:
        path = _write(tmp_path, """
            name: test_metrics_delta
            config:
              MAAS_METRICS_URL: "http://thanos.test/api/v1/query"
            metrics_queries:
              total_requests: "sum(foo)"
            tasks:
              - name: _expect_sent_count
                params: {}
            assertions:
              maas_requests_match:
                compare: metrics.total_requests_delta
                to: inference_results.total_requests
                tolerance_pct: 5
        """)
        result = asyncio.run(ScenarioRunner(path, "metrics-delta-001").run())
        assert result.status == "PASS"
        assertion = {a.name: a for a in result.assertions}["maas_requests_match"]
        assert assertion.status == "PASSING"
        assert assertion.current_value == 45.0  # 55 - 10
        assert assertion.expected_value == 45.0
    finally:
        REGISTRY.pop("_expect_sent_count", None)


def test_configured_max_wait_s_unit() -> None:
    from harness.runner import _METRICS_FINAL_MAX_WAIT_S, _configured_max_wait_s

    assert _configured_max_wait_s({}, []) == _METRICS_FINAL_MAX_WAIT_S

    assert (
        _configured_max_wait_s(
            {"maas_requests_match": {"compare": "a", "to": "b", "max_wait_s": 90}}, []
        )
        == 90.0
    )

    # String values (as produced by ${config.X} interpolation) are coerced to float.
    assert (
        _configured_max_wait_s(
            {}, [{"assertions": {"maas_tokens_match": {"max_wait_s": "120"}}}]
        )
        == 120.0
    )

    # Max across top-level and per-task, and across multiple assertions, wins.
    assert (
        _configured_max_wait_s(
            {"a": {"max_wait_s": 10}},
            [{"assertions": {"b": {"max_wait_s": 30}, "c": {"max_wait_s": 20}}}],
        )
        == 30.0
    )

    # Simple string-form assertions (no dict) and dicts without max_wait_s are ignored.
    assert _configured_max_wait_s({"error_rate_pct": "< 5"}, [{"assertions": {"x": {}}}]) == (
        _METRICS_FINAL_MAX_WAIT_S
    )


def test_scenario_max_wait_s_overrides_default_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scenario-configured max_wait_s is honored instead of the module default."""
    from harness import runner as runner_module

    async def fake_fetch_metrics(base_url: str, queries: dict, token: str) -> dict:
        # Always empty: the assertion can never resolve (stays PENDING forever), so
        # the settle loop can only ever stop by hitting its max_wait_s cap.
        return {}

    monkeypatch.setattr(runner_module, "fetch_metrics", fake_fetch_metrics)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_POLL_INTERVAL_S", 0.01)
    # Deliberately do NOT shrink the module default here — if the scenario's
    # max_wait_s isn't honored, this test would fall back to it and time out slow.
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_MAX_WAIT_S", 5.0)

    path = _write(tmp_path, """
        name: test_max_wait_override
        config:
          MAAS_METRICS_URL: "http://thanos.test/api/v1/query"
        metrics_queries:
          total_requests: "sum(foo)"
        tasks:
          - name: stub_pass
            params: {}
        assertions:
          maas_requests_match:
            compare: metrics.total_requests_delta
            to: inference_results.total_requests
            tolerance_pct: 5
            max_wait_s: 0.05
    """)
    t0 = time.monotonic()
    result = asyncio.run(ScenarioRunner(path, "max-wait-001").run())
    elapsed = time.monotonic() - t0

    assert result.status == "PASS"  # PENDING assertion doesn't fail the run
    assertion = {a.name: a for a in result.assertions}["maas_requests_match"]
    assert assertion.status == "PENDING"
    # Bounded by the scenario's own max_wait_s (0.05s), not the 5s module default.
    assert elapsed < 2.0, f"took {elapsed}s — scenario max_wait_s was not honored"


def test_settle_and_evaluate_polls_until_assertion_actually_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settle loop keeps polling and re-evaluating until the assertion itself
    passes — not until some proxy signal (a changed value, a repeated value, a
    Prometheus sample timestamp) suggests it might have. Earlier designs all tried to
    infer "is the data ready" indirectly and each broke on a different subtlety (see
    ADR-014); checking the actual assertion outcome directly avoids that whole class
    of bug, and matches the requested design: wait up to max_wait_s, but stop as soon
    as it passes.
    """
    from harness import runner as runner_module

    calls: list[None] = []

    async def fake_fetch_metrics(base_url: str, queries: dict, token: str) -> dict:
        calls.append(None)
        n = len(calls)
        if n == 1:
            return {"total_requests": 10.0}  # baseline
        if n <= 3:
            return {"total_requests": 30.0}  # delta=20, outside 5% tolerance of 45 -> FAILING
        return {"total_requests": 55.0}  # delta=45, matches exactly -> PASSING

    monkeypatch.setattr(runner_module, "fetch_metrics", fake_fetch_metrics)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_MAX_WAIT_S", 5.0)

    class _ExpectSentCount(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state["inference_results"] = {"total_requests": 45.0}
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_expect_sent_count_2"] = _ExpectSentCount

    try:
        path = _write(tmp_path, """
            name: test_settle_until_pass
            config:
              MAAS_METRICS_URL: "http://thanos.test/api/v1/query"
            metrics_queries:
              total_requests: "sum(foo)"
            tasks:
              - name: _expect_sent_count_2
                params: {}
            assertions:
              maas_requests_match:
                compare: metrics.total_requests_delta
                to: inference_results.total_requests
                tolerance_pct: 5
        """)
        t0 = time.monotonic()
        result = asyncio.run(ScenarioRunner(path, "settle-001").run())
        elapsed = time.monotonic() - t0

        assert len(calls) >= 4, "must keep polling past a non-passing result"
        assertion = {a.name: a for a in result.assertions}["maas_requests_match"]
        assert assertion.status == "PASSING"
        assert assertion.current_value == 45.0  # 55 - 10, not the earlier 20 (30-10)
        # Stops as soon as it passes — well under the 5s cap, despite needing several
        # non-passing polls first.
        assert elapsed < 2.0, f"took {elapsed}s — did not stop early once passing"
    finally:
        REGISTRY.pop("_expect_sent_count_2", None)


def test_per_task_metrics_assertion_settles_instead_of_failing_instantly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the actual reported bug: a match-assertion attached to a
    *task* (not the scenario's top-level assertions:) must also get the settle
    treatment. It previously didn't — the per-task assertion check did a single
    immediate evaluation with no retry at all, so a metrics-dependent assertion on a
    task failed instantly, before the metrics-settle logic (which only ran after
    cleanup, on top-level assertions) ever got a chance to run.
    """
    from harness import runner as runner_module

    calls: list[None] = []

    async def fake_fetch_metrics(base_url: str, queries: dict, token: str) -> dict:
        calls.append(None)
        n = len(calls)
        if n == 1:
            return {"total_requests": 10.0}  # baseline
        if n <= 2:
            return {"total_requests": 12.0}  # delta=2, well outside tolerance of 20 -> FAILING
        return {"total_requests": 30.0}  # delta=20, matches -> PASSING

    monkeypatch.setattr(runner_module, "fetch_metrics", fake_fetch_metrics)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_MAX_WAIT_S", 5.0)

    class _SendLikeTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state["inference_results"] = {"total_requests": 20.0}
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_send_like_task"] = _SendLikeTask

    try:
        path = _write(tmp_path, """
            name: test_per_task_settle
            config:
              MAAS_METRICS_URL: "http://thanos.test/api/v1/query"
            metrics_queries:
              total_requests: "sum(foo)"
            tasks:
              - name: _send_like_task
                params: {}
                assertions:
                  maas_requests_match:
                    compare: metrics.total_requests_delta
                    to: inference_results.total_requests
                    tolerance_pct: 5
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, "per-task-settle-001").run())

        assert len(calls) >= 3, "per-task assertion check must poll, not just check once"
        assert result.status == "PASS"
        assert result.tasks[0].status == "PASS"
        assertion = {a.name: a for a in result.tasks[0].assertions}["maas_requests_match"]
        assert assertion.status == "PASSING"
        assert assertion.current_value == 20.0  # 30 - 10, the settled value
    finally:
        REGISTRY.pop("_send_like_task", None)


def test_task_progress_stays_visible_during_settle_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: shared_state["task_progress"] must not be popped until AFTER
    a task's metrics-dependent assertion finishes settling, otherwise the UI's task
    progress bar vanishes for the whole settle window (still shown as RUNNING, but
    with no progress data to render) and only reappears once the task is finally
    marked DONE.
    """
    from harness import runner as runner_module

    shared_state_ref: list[dict] = []
    progress_present_during_poll: list[bool] = []
    calls: list[None] = []

    async def fake_fetch_metrics(base_url: str, queries: dict, token: str) -> dict:
        calls.append(None)
        n = len(calls)
        if n > 1:  # skip the pre-run baseline fetch
            progress_present_during_poll.append("task_progress" in shared_state_ref[0])
        return {"total_requests": 10.0} if n == 1 else {"total_requests": 20.0}

    monkeypatch.setattr(runner_module, "fetch_metrics", fake_fetch_metrics)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_MAX_WAIT_S", 5.0)

    class _ProgressTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            shared_state_ref.append(ctx.shared_state)
            ctx.shared_state["task_progress"] = {"current": 5, "total": 5}
            ctx.shared_state["inference_results"] = {"total_requests": 10.0}
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_progress_task"] = _ProgressTask

    try:
        path = _write(tmp_path, """
            name: test_progress_visible
            config:
              MAAS_METRICS_URL: "http://thanos.test/api/v1/query"
            metrics_queries:
              total_requests: "sum(foo)"
            tasks:
              - name: _progress_task
                params: {}
                assertions:
                  maas_requests_match:
                    compare: metrics.total_requests_delta
                    to: inference_results.total_requests
                    tolerance_pct: 5
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, "progress-visible-001").run())

        assert result.status == "PASS"
        assert progress_present_during_poll, "settle loop never actually polled"
        assert all(progress_present_during_poll), (
            "task_progress was popped from shared_state before settling finished"
        )
    finally:
        REGISTRY.pop("_progress_task", None)


def test_metrics_polling_disabled_without_config(tmp_path: Path) -> None:
    """No MAAS_METRICS_URL/QUERIES configured: match assertion stays PENDING, run still passes."""
    path = _write(tmp_path, """
        name: test_no_metrics
        config: {}
        tasks:
          - name: stub_pass
            params: {}
        assertions:
          maas_requests_match:
            compare: metrics.total_requests_delta
            to: inference_results.total_requests
            tolerance_pct: 5
    """)
    result = asyncio.run(ScenarioRunner(path, "no-metrics-001").run())
    assert result.status == "PASS"
    assertion = {a.name: a for a in result.assertions}["maas_requests_match"]
    assert assertion.status == "PENDING"


def test_substitute_baseline_vars_unit() -> None:
    from harness.runner import _substitute_baseline_vars

    assert (
        _substitute_baseline_vars(
            "sum(foo) - ${baseline.total_requests}", {"total_requests": 10.0}, {"total_requests"}
        )
        == "sum(foo) - 10.0"
    )
    # A template with no ${baseline.x} at all is returned unchanged.
    assert _substitute_baseline_vars("sum(foo)", {}, set()) == "sum(foo)"


def test_substitute_baseline_vars_raises_on_undeclared_name() -> None:
    """Referencing a name never declared in metrics_queries at all is a genuine
    authoring mistake (a typo) — this should still fail loudly at scenario start."""
    from harness.runner import _substitute_baseline_vars

    with pytest.raises(KeyError, match="not declared"):
        _substitute_baseline_vars("sum(foo) - ${baseline.total_requests}", {}, set())


def test_substitute_baseline_vars_returns_none_when_declared_but_no_data(
    capsys: pytest.CaptureFixture,
) -> None:
    """A declared query whose baseline fetch came back empty (e.g. a label filter
    that matches nothing on this cluster) must NOT crash the whole run — it should
    degrade to None (permanently PENDING for this run) with a diagnostic printed,
    the same way every other 'metric not populated yet' case in this codebase does.
    """
    from harness.runner import _substitute_baseline_vars

    resolved = _substitute_baseline_vars(
        "sum(foo) - ${baseline.total_requests}", {}, {"total_requests"}
    )
    assert resolved is None
    out = capsys.readouterr().out
    assert "total_requests" in out
    assert "no data at scenario start" in out


def test_substitute_harness_vars_unit() -> None:
    from harness.runner import _substitute_harness_vars

    state = {"inference_results": {"total_requests": 45.0}}
    resolved = _substitute_harness_vars(
        "abs(x - ${harness.inference_results.total_requests})", state
    )
    assert resolved == "abs(x - 45.0)"


def test_substitute_harness_vars_returns_none_when_missing() -> None:
    from harness.runner import _substitute_harness_vars

    assert _substitute_harness_vars("x - ${harness.inference_results.total_requests}", {}) is None


def test_promql_assertion_resolves_baseline_and_harness_templates_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full ADR-015 wiring: a promql-form assertion referencing ${baseline.x}
    (resolved once from the scenario's metrics_queries baseline snapshot) and
    ${harness.x} (resolved fresh each tick from live shared_state) ends up PASSING
    once both are populated, without the runner needing any query-specific code.
    """
    from harness import runner as runner_module

    seen_queries: list[dict] = []
    calls: list[None] = []

    async def fake_fetch_metrics(base_url: str, queries: dict, token: str) -> dict:
        calls.append(None)
        seen_queries.append(dict(queries))
        if len(calls) == 1:
            return {"total_requests": 10.0}  # baseline
        return {"total_requests": 55.0, "maas_requests_match": 1.0}

    monkeypatch.setattr(runner_module, "fetch_metrics", fake_fetch_metrics)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_MAX_WAIT_S", 0.05)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_POLL_INTERVAL_S", 0.01)

    class _ExpectSentCount(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state["inference_results"] = {"total_requests": 45.0}
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_promql_template_task"] = _ExpectSentCount

    try:
        path = _write(tmp_path, """
            name: test_promql_templating
            config:
              MAAS_METRICS_URL: "http://thanos.test/api/v1/query"
            metrics_queries:
              total_requests: "sum(foo)"
            tasks:
              - name: _promql_template_task
                params: {}
            assertions:
              maas_requests_match:
                promql: "abs(sum(foo) - ${baseline.total_requests} - ${harness.inference_results.total_requests}) <= bool 0"
                expect: "== 1"
        """)
        result = asyncio.run(ScenarioRunner(path, "promql-template-001").run())

        assert result.status == "PASS"
        assertion = {a.name: a for a in result.assertions}["maas_requests_match"]
        assert assertion.status == "PASSING"
        assert assertion.current_value == 1.0

        # The pre-run baseline fetch only ever queries the named metrics_queries —
        # promql-form assertions aren't part of it (there's nothing to baseline-
        # substitute against yet).
        assert seen_queries[0] == {"total_requests": "sum(foo)"}
        # Once inference_results is populated, both template variables resolve to
        # literals in the fired query text.
        assert any(
            q.get("maas_requests_match")
            == "abs(sum(foo) - 10.0 - 45.0) <= bool 0"
            for q in seen_queries[1:]
        )
    finally:
        REGISTRY.pop("_promql_template_task", None)


def test_baseline_query_returning_no_data_does_not_crash_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real reported bug: a declared metrics_queries entry
    whose baseline fetch comes back empty (e.g. a limitador_namespace label filter
    that doesn't match anything on this cluster) must not raise out of the whole
    run before a single task executes — the run should complete normally, with only
    the assertion referencing that baseline value stuck PENDING.
    """
    from harness import runner as runner_module

    async def fake_fetch_metrics(base_url: str, queries: dict, token: str) -> dict:
        # total_requests never resolves — simulating a label filter that matches no
        # series on this cluster. Any other query (like the harness pass-through
        # error_rate_pct assertion) succeeds normally, same as real behavior where
        # only the mismatched query comes back empty.
        return {"error_rate_pct": 1.0} if "error_rate_pct" in queries else {}

    monkeypatch.setattr(runner_module, "fetch_metrics", fake_fetch_metrics)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_MAX_WAIT_S", 0.05)
    monkeypatch.setattr(runner_module, "_METRICS_FINAL_POLL_INTERVAL_S", 0.01)

    class _SendLikeTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state["inference_results"] = {"error_rate_pct": 1.0}
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_no_baseline_data_task"] = _SendLikeTask

    try:
        path = _write(tmp_path, """
            name: test_baseline_no_data
            config:
              MAAS_METRICS_URL: "http://thanos.test/api/v1/query"
            metrics_queries:
              total_requests: "sum(foo)"
            tasks:
              - name: _no_baseline_data_task
                params: {}
            assertions:
              maas_requests_match:
                promql: "sum(foo) - ${baseline.total_requests}"
                expect: "== 0"
              error_rate_pct:
                promql: "${harness.inference_results.error_rate_pct}"
                expect: "< 5"
        """)
        result = asyncio.run(ScenarioRunner(path, "baseline-no-data-001").run())

        assert result.status == "PASS"
        assertions = {a.name: a for a in result.assertions}
        assert assertions["maas_requests_match"].status == "PENDING"
        assert assertions["error_rate_pct"].status == "PASSING"
    finally:
        REGISTRY.pop("_no_baseline_data_task", None)


def test_unknown_task_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, """
        name: test_unknown
        config: {}
        tasks:
          - name: nonexistent_task
            params: {}
        assertions: {}
    """)
    with pytest.raises(KeyError):
        asyncio.run(ScenarioRunner(path, "unknown-001").run())


def test_stub_scenario_passes() -> None:
    result = asyncio.run(ScenarioRunner("scenarios/stub.yaml", "stub-001").run())
    assert result.status == "PASS"
    assert len(result.tasks) == 2
    assert all(t.status == "PASS" for t in result.tasks)


def test_per_task_assertion_fail_aborts_run(tmp_path: Path) -> None:
    """A failing per-task assertion overrides task to FAIL and aborts subsequent tasks."""
    ran: list[str] = []

    class _FirstTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ran.append("first")
            ctx.shared_state.setdefault("inference_results", {})["error_rate_pct"] = 99.0
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    class _SecondTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ran.append("second")
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_per_task_first"] = _FirstTask
    REGISTRY["_per_task_second"] = _SecondTask

    try:
        path = _write(tmp_path, """
            name: test_per_task_fail
            config: {}
            tasks:
              - name: _per_task_first
                params: {}
                assertions:
                  error_rate_pct: "< 5"
              - name: _per_task_second
                params: {}
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, "per-task-001").run())
        assert result.status == "FAIL"
        assert ran == ["first"], "second task must not run after first task's assertions fail"
        assert result.tasks[0].status == "FAIL"
        assert result.tasks[0].error == "assertions failed at task completion"
        assert len(result.tasks[0].assertions) == 1
        assert result.tasks[0].assertions[0].name == "error_rate_pct"
        assert result.tasks[0].assertions[0].status == "FAILING"
    finally:
        REGISTRY.pop("_per_task_first", None)
        REGISTRY.pop("_per_task_second", None)


def test_per_task_assertion_pass_continues_run(tmp_path: Path) -> None:
    """Passing per-task assertions don't abort the run; assertions stored on TaskResult."""
    ran: list[str] = []

    class _GoodTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ran.append(self.name)
            ctx.shared_state.setdefault("inference_results", {})["error_rate_pct"] = 1.0
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_good_task_a"] = _GoodTask
    REGISTRY["_good_task_b"] = _GoodTask

    try:
        path = _write(tmp_path, """
            name: test_per_task_pass
            config: {}
            tasks:
              - name: _good_task_a
                params: {}
                assertions:
                  error_rate_pct: "< 5"
              - name: _good_task_b
                params: {}
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, "per-task-pass-001").run())
        assert result.status == "PASS"
        assert ran == ["_good_task_a", "_good_task_b"]
        assert result.tasks[0].assertions[0].status == "PASSING"
        assert result.tasks[1].assertions == []
    finally:
        REGISTRY.pop("_good_task_a", None)
        REGISTRY.pop("_good_task_b", None)


def test_stub_failing_scenario_produces_fail_with_cleanup(
    capsys: pytest.CaptureFixture,
) -> None:
    result = asyncio.run(ScenarioRunner("scenarios/stub_failing.yaml", "stubfail-001").run())
    assert result.status == "FAIL"
    out = capsys.readouterr().out
    assert "[stub_pass] cleanup" in out
    assert "[stub_fail] cleanup" in out


def test_run_result_has_positive_duration_ms() -> None:
    result = asyncio.run(ScenarioRunner("scenarios/stub.yaml", "duration-001").run())
    assert result.status == "PASS"
    assert result.duration_ms > 0


def test_stop_event_cancels_in_flight_task_and_still_runs_cleanup(tmp_path: Path) -> None:
    """A stop request mid-task cancels that task's in-flight run() (rather than
    waiting for it to finish on its own) and still falls through to the normal
    cleanup loop — the whole point of a *graceful* stop.
    """
    cleaned: list[str] = []

    class _LongRunningTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            await asyncio.sleep(10)  # would hang this test if not actually cancelled
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            cleaned.append(self.name)

    REGISTRY["_long_running"] = _LongRunningTask

    try:
        path = _write(tmp_path, """
            name: test_stop_mid_task
            config: {}
            tasks:
              - name: _long_running
                params: {}
            assertions: {}
        """)

        async def _drive():
            stop_event = asyncio.Event()
            run_future = asyncio.ensure_future(
                ScenarioRunner(path, "stop-001", stop_event=stop_event).run()
            )
            await asyncio.sleep(0.05)
            stop_event.set()
            return await run_future

        t0 = time.monotonic()
        result = asyncio.run(_drive())
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, "stop did not interrupt the in-flight task promptly"
        assert result.status == "CANCELLED"
        assert len(result.tasks) == 1
        assert result.tasks[0].status == "CANCELLED"
        assert result.tasks[0].error == "run stopped by user"
        assert cleaned == ["_long_running"], "cleanup must still run for a cancelled task"
    finally:
        REGISTRY.pop("_long_running", None)


def test_stop_event_set_before_run_skips_all_tasks(tmp_path: Path) -> None:
    ran: list[str] = []

    class _ShouldNotRun(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ran.append(self.name)
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_should_not_run"] = _ShouldNotRun

    try:
        path = _write(tmp_path, """
            name: test_stop_before_start
            config: {}
            tasks:
              - name: _should_not_run
                params: {}
            assertions: {}
        """)
        stop_event = asyncio.Event()
        stop_event.set()
        result = asyncio.run(ScenarioRunner(path, "stop-002", stop_event=stop_event).run())

        assert result.status == "CANCELLED"
        assert ran == [], "a task must not start once a stop has already been requested"
        assert result.tasks == []
    finally:
        REGISTRY.pop("_should_not_run", None)


def test_progress_json_includes_run_started_at_and_task_durations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)

    result = asyncio.run(ScenarioRunner("scenarios/stub.yaml", "progress-fields-001").run())
    assert result.status == "PASS"

    payload = json.loads((tmp_path / "progress-fields-001-progress.json").read_text())
    assert "run_started_at" in payload
    assert payload["run_started_at"]
    for entry in payload["tasks"]:
        assert entry["status"] == "DONE"
        assert "duration_ms" in entry


def test_config_snapshot_is_scenario_shaped_and_redacts_top_level_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The snapshot must be shaped like an actual scenario file (name/config/
    tasks/cleanup), not a flat list — the whole point is that a user can paste it
    into a new scenarios/*.yaml file to reproduce the run exactly."""
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)

    path = _write(tmp_path, """
        name: test_config_snapshot
        description: "a test scenario"
        config:
          request_count: 5
          api_token: "supersecret"
        tasks:
          - name: stub_pass
            params: {}
        assertions: {}
        cleanup: automatic
    """)
    result = asyncio.run(ScenarioRunner(path, "config-snap-001").run())
    assert result.status == "PASS"

    snapshot = json.loads((tmp_path / "config-snap-001-config.json").read_text())
    assert snapshot["name"] == "test_config_snapshot"
    assert snapshot["description"] == "a test scenario"
    assert snapshot["cleanup"] == "automatic"
    assert snapshot["tasks"][0]["name"] == "stub_pass"
    assert snapshot["config"]["request_count"] == 5
    assert snapshot["config"]["api_token"] == "***REDACTED***"


def test_config_snapshot_redacts_sensitive_values_inside_task_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolved ${config.target_token}-style value can end up inside a task's
    params, not just the top-level config: block — redaction must be recursive."""
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)

    path = _write(tmp_path, """
        name: test_task_param_redaction
        config:
          target_token: "supersecret"
        tasks:
          - name: stub_pass
            params:
              token: "${config.target_token}"
        assertions: {}
    """)
    result = asyncio.run(ScenarioRunner(path, "config-snap-002").run())
    assert result.status == "PASS"

    snapshot = json.loads((tmp_path / "config-snap-002-config.json").read_text())
    assert snapshot["tasks"][0]["params"]["token"] == "***REDACTED***"


def test_config_snapshot_does_not_redact_token_limit_or_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """token_limit/token_window (the rate-limit scenarios' MaaS subscription
    config — an LLM token budget and a time window, not a credential) are a
    named exception: "token" is a complete word in them too, but they aren't
    secrets and a user needs to see them to understand what a run actually
    tested. Real token credentials (target_token here) must still redact."""
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)

    path = _write(tmp_path, """
        name: test_token_limit_not_redacted
        config:
          token_limit: 50
          token_window: "24h"
          target_token: "supersecret"
        tasks:
          - name: stub_pass
            params: {}
        assertions: {}
    """)
    result = asyncio.run(ScenarioRunner(path, "config-snap-003").run())
    assert result.status == "PASS"

    snapshot = json.loads((tmp_path / "config-snap-003-config.json").read_text())
    assert snapshot["config"]["token_limit"] == 50
    assert snapshot["config"]["token_window"] == "24h"
    assert snapshot["config"]["target_token"] == "***REDACTED***"


def test_config_snapshot_excludes_incidental_environment_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolved_config is a merge of the *entire* process environment (see
    harness/config.py:load_scenario) — the snapshot's config: block must narrow
    that down to the scenario's own declared config: keys plus curated global
    settings, not dump every container-plumbing env var (PATH, HOSTNAME, etc.).
    """
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)
    monkeypatch.setenv("MAAS_API_URL", "https://maas.example.com")
    monkeypatch.setenv("SOME_UNRELATED_ENV_VAR", "noise")

    path = _write(tmp_path, """
        name: test_config_noise
        config:
          request_count: 5
        tasks:
          - name: stub_pass
            params: {}
        assertions: {}
    """)
    result = asyncio.run(ScenarioRunner(path, "config-noise-001").run())
    assert result.status == "PASS"

    config = json.loads((tmp_path / "config-noise-001-config.json").read_text())["config"]
    assert config["request_count"] == 5
    assert config["MAAS_API_URL"] == "https://maas.example.com"
    assert "SOME_UNRELATED_ENV_VAR" not in config
    assert "PATH" not in config
    assert "PATH" not in config


def test_auto_cleanup_flag_false_skips_cleanup_and_writes_skipped_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness import cleanup_state
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)
    run_id = "auto-cleanup-off-001"
    cleanup_state.write_auto_cleanup_flag(tmp_path, run_id, False)

    cleaned: list[str] = []

    class _Tracked(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state["api_keys"] = [{"id": "k1"}]
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            cleaned.append(self.name)

    REGISTRY["_ac_tracked"] = _Tracked
    try:
        path = _write(tmp_path, """
            name: test_auto_cleanup_off
            config: {}
            tasks:
              - name: _ac_tracked
                params: {}
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, run_id).run())
        assert result.status == "PASS"
        assert cleaned == [], "cleanup() must not run when the flag is off"

        status = json.loads((tmp_path / f"{run_id}-cleanup-status.json").read_text())
        assert status["status"] == "skipped"

        # shared_state must still be persisted so a later manual cleanup has
        # what it needs, even though automatic cleanup was skipped.
        state = json.loads((tmp_path / f"{run_id}-cleanup-state.json").read_text())
        assert state["api_keys"] == [{"id": "k1"}]
    finally:
        REGISTRY.pop("_ac_tracked", None)


def test_auto_cleanup_flag_missing_defaults_true_and_runs_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No flag file at all (e.g. a run predating this feature) must behave
    exactly like today's unconditional cleanup, not silently skip it."""
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)
    run_id = "auto-cleanup-default-001"

    cleaned: list[str] = []

    class _Tracked(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            cleaned.append(self.name)

    REGISTRY["_ac_default"] = _Tracked
    try:
        path = _write(tmp_path, """
            name: test_auto_cleanup_default
            config: {}
            tasks:
              - name: _ac_default
                params: {}
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, run_id).run())
        assert result.status == "PASS"
        assert cleaned == ["_ac_default"]

        status = json.loads((tmp_path / f"{run_id}-cleanup-status.json").read_text())
        assert status["status"] == "done"
    finally:
        REGISTRY.pop("_ac_default", None)


def test_auto_cleanup_task_failure_writes_failed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)
    run_id = "auto-cleanup-fail-001"

    class _BrokenCleanup(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            raise RuntimeError("cleanup exploded")

    REGISTRY["_ac_broken"] = _BrokenCleanup
    try:
        path = _write(tmp_path, """
            name: test_auto_cleanup_fail
            config: {}
            tasks:
              - name: _ac_broken
                params: {}
            assertions: {}
        """)
        result = asyncio.run(ScenarioRunner(path, run_id).run())
        # A cleanup failure is logged, not fatal to the run's own PASS/FAIL status
        # (matches the pre-existing unconditional-cleanup behavior).
        assert result.status == "PASS"

        status = json.loads((tmp_path / f"{run_id}-cleanup-status.json").read_text())
        assert status["status"] == "failed"
    finally:
        REGISTRY.pop("_ac_broken", None)


def test_progress_json_carries_narration_traffic_resources_and_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything the run page needs to explain a run, in one file: per-task
    narration, a traffic summary/timeline, what was created (names only —
    never key material), and a final plain-language verdict."""
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path / "results")

    class _NarratingTask(Task):
        async def run(self, ctx: TaskContext) -> TaskResult:
            ctx.shared_state["api_keys"] = [
                {"id": "k1", "name": "maaspal-key-1", "key": "sk-oai-SECRET", "subscription": "free"}
            ]
            ctx.shared_state["inference_results"] = {"total_requests": 2, "tokens_before_first_429": 57}
            ctx.shared_state["_traffic"] = {
                "inference_results": {
                    "task": self.name,
                    "planned": 2,
                    "timeline": [[0.1, 30, "ok", 10.0], [0.2, 57, "throttled", 5.0]],
                }
            }
            ctx.shared_state["task_summary"] = "did the thing"
            return TaskResult(task_name=self.name, status="PASS", duration_ms=0)

        async def cleanup(self, ctx: TaskContext) -> None:
            pass

    REGISTRY["_narrating"] = _NarratingTask
    try:
        path = _write(tmp_path, """
            name: test_narration
            config:
              token_limit: 50
            tasks:
              - name: _narrating
                params: {}
            verdict:
              pass: "Throttled after ${harness.inference_results.tokens_before_first_429} tokens (limit ${config.token_limit})."
              fail: "Not throttled as expected."
        """)
        result = asyncio.run(ScenarioRunner(path, "narr-001").run())
    finally:
        REGISTRY.pop("_narrating", None)

    assert result.status == "PASS"
    raw = (tmp_path / "results" / "narr-001-progress.json").read_text()
    assert "sk-oai-SECRET" not in raw
    payload = json.loads(raw)

    assert payload["tasks"][0]["summary"] == "did the thing"
    traffic = payload["traffic"][0]
    assert traffic["result_key"] == "inference_results"
    assert traffic["limit"] == 50  # the scenario's token_limit, for the chart's reference line
    assert traffic["summary"]["tokens_before_first_429"] == 57
    assert [p[2] for p in traffic["timeline"]] == ["ok", "throttled"]
    assert payload["resources"] == [
        {"kind": "API key", "name": "maaspal-key-1", "subscription": "free", "action": "created"}
    ]
    assert payload["verdict"]["status"] == "PASS"
    assert payload["verdict"]["text"] == "Throttled after 57 tokens (limit 50)."


def test_verdict_falls_back_to_check_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from harness import runner as runner_module

    monkeypatch.setattr(runner_module, "_RESULTS_DIR", tmp_path)
    asyncio.run(ScenarioRunner("scenarios/stub.yaml", "verdict-fallback-001").run())
    payload = json.loads((tmp_path / "verdict-fallback-001-progress.json").read_text())
    assert payload["verdict"]["text"] == "Run completed — this scenario defines no checks."


def test_downsample_timeline_keeps_status_transitions() -> None:
    from harness.runner import _TIMELINE_MAX_POINTS, _downsample_timeline

    timeline = [[i, i, "ok", 1.0] for i in range(3000)]
    timeline[1501][2] = "throttled"  # a lone transition a uniform stride would skip
    thinned = _downsample_timeline(timeline)
    assert len(thinned) <= _TIMELINE_MAX_POINTS + 10
    assert any(p[2] == "throttled" for p in thinned)
    assert thinned[0] == timeline[0] and thinned[-1] == timeline[-1]
