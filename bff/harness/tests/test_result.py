import pytest

from harness.result import (
    AssertionResult,
    RunResult,
    TaskResult,
    compute_run_status,
    evaluate_all_assertions,
    evaluate_assertion,
)


def test_pending_when_metric_absent() -> None:
    r = evaluate_assertion("error_rate_pct", "< 5", shared_state={})
    assert r.status == "PENDING"
    assert r.current_value is None


def test_passing_less_than() -> None:
    state = {"inference_results": {"error_rate_pct": 2.0}}
    r = evaluate_assertion("error_rate_pct", "< 5", state)
    assert r.status == "PASSING"
    assert r.current_value == 2.0


def test_failing_less_than() -> None:
    state = {"inference_results": {"error_rate_pct": 10.0}}
    assert evaluate_assertion("error_rate_pct", "< 5", state).status == "FAILING"


def test_greater_than_passing() -> None:
    state = {"inference_results": {"throughput_rps": 15.0}}
    assert evaluate_assertion("throughput_rps", "> 10", state).status == "PASSING"


def test_greater_than_failing() -> None:
    state = {"inference_results": {"throughput_rps": 5.0}}
    assert evaluate_assertion("throughput_rps", "> 10", state).status == "FAILING"


def test_less_than_or_equal_passing() -> None:
    state = {"inference_results": {"throughput_rps": 10.0}}
    assert evaluate_assertion("throughput_rps", "<= 10", state).status == "PASSING"


def test_less_than_or_equal_failing() -> None:
    state = {"inference_results": {"throughput_rps": 11.0}}
    assert evaluate_assertion("throughput_rps", "<= 10", state).status == "FAILING"


def test_greater_than_or_equal_passing() -> None:
    state = {"inference_results": {"total_requests": 95.0}}
    assert evaluate_assertion("total_requests", ">= 95", state).status == "PASSING"


def test_greater_than_or_equal_failing() -> None:
    state = {"inference_results": {"total_requests": 94.0}}
    assert evaluate_assertion("total_requests", ">= 95", state).status == "FAILING"


def test_equal_passing() -> None:
    state = {"inference_results": {"fail_count": 0.0}}
    assert evaluate_assertion("fail_count", "== 0", state).status == "PASSING"


def test_equal_failing() -> None:
    state = {"inference_results": {"fail_count": 1.0}}
    assert evaluate_assertion("fail_count", "== 0", state).status == "FAILING"


def test_invalid_expression_raises() -> None:
    state = {"inference_results": {"x": 1.0}}
    with pytest.raises(ValueError, match="Cannot parse assertion expression"):
        evaluate_assertion("x", "!= 5", state)


def test_metrics_namespace() -> None:
    state = {"metrics": {"total_tokens": 500.0}}
    r = evaluate_assertion("total_tokens", ">= 100", state)
    assert r.status == "PASSING"


def test_evaluate_all_mixed_states() -> None:
    state = {"inference_results": {"error_rate_pct": 2.0}}
    assertions = {"error_rate_pct": "< 5", "missing_metric": "> 0"}
    results = {r.name: r for r in evaluate_all_assertions(assertions, state)}
    assert results["error_rate_pct"].status == "PASSING"
    assert results["missing_metric"].status == "PENDING"


def test_match_assertion_pending_when_observed_missing() -> None:
    state = {"inference_results": {"total_requests": 100.0}}
    spec = {"compare": "metrics.total_requests_delta", "to": "inference_results.total_requests", "tolerance_pct": 5}
    r = evaluate_assertion("maas_requests_match", spec, state)
    assert r.status == "PENDING"
    assert r.current_value is None


def test_match_assertion_pending_when_expected_missing() -> None:
    state = {"metrics": {"total_requests_delta": 100.0}}
    spec = {"compare": "metrics.total_requests_delta", "to": "inference_results.total_requests", "tolerance_pct": 5}
    r = evaluate_assertion("maas_requests_match", spec, state)
    assert r.status == "PENDING"


def test_match_assertion_passing_within_tolerance() -> None:
    state = {
        "metrics": {"total_requests_delta": 102.0},
        "inference_results": {"total_requests": 100.0},
    }
    spec = {"compare": "metrics.total_requests_delta", "to": "inference_results.total_requests", "tolerance_pct": 5}
    r = evaluate_assertion("maas_requests_match", spec, state)
    assert r.status == "PASSING"
    assert r.current_value == 102.0
    assert r.expected_value == 100.0


def test_match_assertion_failing_outside_tolerance() -> None:
    state = {
        "metrics": {"total_requests_delta": 150.0},
        "inference_results": {"total_requests": 100.0},
    }
    spec = {"compare": "metrics.total_requests_delta", "to": "inference_results.total_requests", "tolerance_pct": 5}
    r = evaluate_assertion("maas_requests_match", spec, state)
    assert r.status == "FAILING"


def test_match_assertion_exact_boundary_passes() -> None:
    state = {
        "metrics": {"total_requests_delta": 105.0},
        "inference_results": {"total_requests": 100.0},
    }
    spec = {"compare": "metrics.total_requests_delta", "to": "inference_results.total_requests", "tolerance_pct": 5}
    assert evaluate_assertion("maas_requests_match", spec, state).status == "PASSING"


def test_match_assertion_zero_expected_uses_absolute_floor() -> None:
    """With expected == 0, tolerance is computed against a floor of 1, not 0."""
    state = {
        "metrics": {"total_requests_delta": 0.0},
        "inference_results": {"total_requests": 0.0},
    }
    spec = {"compare": "metrics.total_requests_delta", "to": "inference_results.total_requests", "tolerance_pct": 5}
    assert evaluate_assertion("maas_requests_match", spec, state).status == "PASSING"


def test_evaluate_all_assertions_mixed_string_and_dict_forms() -> None:
    state = {
        "inference_results": {"error_rate_pct": 1.0, "total_requests": 100.0},
        "metrics": {"total_requests_delta": 100.0},
    }
    assertions = {
        "error_rate_pct": "< 5",
        "maas_requests_match": {
            "compare": "metrics.total_requests_delta",
            "to": "inference_results.total_requests",
            "tolerance_pct": 5,
        },
    }
    results = {r.name: r for r in evaluate_all_assertions(assertions, state)}
    assert results["error_rate_pct"].status == "PASSING"
    assert results["maas_requests_match"].status == "PASSING"


def test_promql_assertion_pending_when_not_yet_fetched() -> None:
    spec = {"promql": "sum(foo)", "expect": "< 5"}
    r = evaluate_assertion("queue_depth_ok", spec, shared_state={})
    assert r.status == "PENDING"
    assert r.current_value is None


def test_promql_assertion_expect_form_passing() -> None:
    state = {"metrics": {"queue_depth_ok": 3.0}}
    spec = {"promql": "sum(foo)", "expect": "< 5"}
    r = evaluate_assertion("queue_depth_ok", spec, state)
    assert r.status == "PASSING"
    assert r.current_value == 3.0


def test_promql_assertion_expect_form_failing() -> None:
    state = {"metrics": {"queue_depth_ok": 9.0}}
    spec = {"promql": "sum(foo)", "expect": "< 5"}
    assert evaluate_assertion("queue_depth_ok", spec, state).status == "FAILING"


def test_promql_assertion_requires_expect_or_compare_to() -> None:
    state = {"metrics": {"x": 1.0}}
    spec = {"promql": "sum(foo)"}
    with pytest.raises(ValueError, match="needs one of 'expect', 'between' or 'compare_to'"):
        evaluate_assertion("x", spec, state)


def test_promql_assertion_compare_to_pending_when_harness_value_missing() -> None:
    state = {"metrics": {"maas_requests_match": 45.0}}
    spec = {"promql": "sum(foo) - 10", "compare_to": "inference_results.total_requests", "tolerance_pct": 5}
    r = evaluate_assertion("maas_requests_match", spec, state)
    assert r.status == "PENDING"


def test_promql_assertion_compare_to_passing_within_tolerance() -> None:
    state = {
        "metrics": {"maas_requests_match": 102.0},
        "inference_results": {"total_requests": 100.0},
    }
    spec = {"promql": "sum(foo) - 10", "compare_to": "inference_results.total_requests", "tolerance_pct": 5}
    r = evaluate_assertion("maas_requests_match", spec, state)
    assert r.status == "PASSING"
    assert r.current_value == 102.0
    assert r.expected_value == 100.0


def test_promql_assertion_compare_to_failing_outside_tolerance() -> None:
    state = {
        "metrics": {"maas_requests_match": 150.0},
        "inference_results": {"total_requests": 100.0},
    }
    spec = {"promql": "sum(foo) - 10", "compare_to": "inference_results.total_requests", "tolerance_pct": 5}
    assert evaluate_assertion("maas_requests_match", spec, state).status == "FAILING"


def test_promql_assertion_self_contained_bool_form() -> None:
    """A promql string that already does its own <bool> comparison server-side —
    the fully self-contained form ADR-015 targets — just needs expect: '== 1'."""
    state = {"metrics": {"maas_requests_match": 1.0}}
    spec = {"promql": "abs(sum(foo) - 45) <= bool (0.05 * 45)", "expect": "== 1"}
    assert evaluate_assertion("maas_requests_match", spec, state).status == "PASSING"


def test_run_status_pass() -> None:
    tasks = [TaskResult("t1", "PASS", 100.0)]
    assertions = [AssertionResult("x", "< 5", "PASSING", 2.0)]
    assert compute_run_status(tasks, assertions) == "PASS"


def test_run_status_pass_no_assertions() -> None:
    tasks = [TaskResult("t1", "PASS", 100.0)]
    assert compute_run_status(tasks, []) == "PASS"


def test_run_status_fail_on_task_failure() -> None:
    tasks = [TaskResult("t1", "FAIL", 100.0, error="oops")]
    assert compute_run_status(tasks, []) == "FAIL"


def test_run_status_fail_on_failing_assertion() -> None:
    tasks = [TaskResult("t1", "PASS", 100.0)]
    assertions = [AssertionResult("x", "< 5", "FAILING", 10.0)]
    assert compute_run_status(tasks, assertions) == "FAIL"


def test_run_status_pass_with_pending_assertions() -> None:
    tasks = [TaskResult("t1", "PASS", 100.0)]
    assertions = [AssertionResult("x", "< 5", "PENDING")]
    assert compute_run_status(tasks, assertions) == "PASS"


def test_run_result_duration_ms_defaults_to_zero() -> None:
    result = RunResult(run_id="r1", scenario_name="s", status="PASS")
    assert result.duration_ms == 0.0


def test_run_result_duration_ms_passthrough() -> None:
    result = RunResult(run_id="r1", scenario_name="s", status="PASS", duration_ms=1234.5)
    assert result.duration_ms == 1234.5


def test_task_result_accepts_cancelled_status() -> None:
    t = TaskResult(task_name="t1", status="CANCELLED", duration_ms=50.0, error="run stopped by user")
    assert t.status == "CANCELLED"


def test_run_result_accepts_cancelled_status() -> None:
    result = RunResult(run_id="r1", scenario_name="s", status="CANCELLED")
    assert result.status == "CANCELLED"


def test_promql_between_passes_inside_range_and_shows_real_value() -> None:
    state = {"metrics": {"throttled_at": 57.0}}
    spec = {"promql": "x", "between": ["50", "50 + 100"]}
    r = evaluate_assertion("throttled_at", spec, state)
    assert r.status == "PASSING"
    assert r.current_value == 57.0
    assert r.target == "50 – 150"


@pytest.mark.parametrize("observed", [49.0, 151.0])
def test_promql_between_fails_outside_range(observed: float) -> None:
    state = {"metrics": {"throttled_at": observed}}
    r = evaluate_assertion("throttled_at", {"promql": "x", "between": [50, "50 + 100"]}, state)
    assert r.status == "FAILING"


def test_promql_between_pending_still_shows_target() -> None:
    r = evaluate_assertion("throttled_at", {"promql": "x", "between": [50, 150]}, {})
    assert r.status == "PENDING"
    assert r.target == "50 – 150"


def test_expect_rhs_accepts_simple_arithmetic() -> None:
    state = {"metrics": {"x": 120.0}}
    r = evaluate_assertion("x", {"promql": "x", "expect": "<= 50 + 100"}, state)
    assert r.status == "PASSING"
    assert r.target == "<= 150"


def test_bound_rejects_non_arithmetic() -> None:
    state = {"metrics": {"x": 1.0}}
    with pytest.raises(ValueError, match="Unsupported assertion bound"):
        evaluate_assertion("x", {"promql": "x", "between": ["__import__('os')", 2]}, state)


def test_display_metadata_passed_through() -> None:
    state = {"metrics": {"x": 1.0}}
    spec = {
        "promql": "x",
        "expect": "== 1",
        "label": "Throttled at budget",
        "description": "Why it matters",
        "unit": "tokens",
    }
    r = evaluate_assertion("x", spec, state)
    assert (r.label, r.description, r.unit) == ("Throttled at budget", "Why it matters", "tokens")
    assert r.target == "== 1"


def test_simple_form_has_target_and_no_label() -> None:
    r = evaluate_assertion("error_rate_pct", "< 5", {"inference_results": {"error_rate_pct": 1.0}})
    assert r.target == "< 5"
    assert r.label is None


def test_bounds_can_reference_live_harness_values() -> None:
    """The limit under test may only be known at run time (read off the
    subscription), so bounds accept ${harness.ns.key} as well as numbers."""
    spec = {
        "promql": "x",
        "between": ["${harness.subscription_limits.token_limit}", "${harness.subscription_limits.token_limit} + 100"],
    }
    pending = evaluate_assertion("x", spec, {"metrics": {"x": 120.0}})
    assert pending.status == "PENDING"  # limit not read yet

    state = {"metrics": {"x": 120.0}, "subscription_limits": {"token_limit": 100}}
    r = evaluate_assertion("x", spec, state)
    assert r.status == "PASSING"
    assert r.target == "100 – 200"


def test_expect_can_reference_live_harness_values() -> None:
    state = {"metrics": {"x": 3.0}, "users": {"count": 2}}
    r = evaluate_assertion("x", {"promql": "x", "expect": "> ${harness.users.count}"}, state)
    assert r.status == "PASSING"
