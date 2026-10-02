import ast
import operator
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

AssertionStatus = Literal["PENDING", "PASSING", "FAILING"]

_OPERATORS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
}
_EXPR_RE = re.compile(r"^\s*(<=|>=|<|>|==)\s*(.+?)\s*$")

_ARITH_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _num(value: Any) -> float:
    """A bound as a number — either a plain number, or simple arithmetic left
    over from ${config.x} substitution (e.g. "50 + 100" from
    "${config.token_limit} + 100"). Parsed with a tiny AST walker, never eval."""
    if isinstance(value, (int, float)):
        return float(value)

    def _walk(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -_walk(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in _ARITH_OPS:
            return _ARITH_OPS[type(node.op)](_walk(node.left), _walk(node.right))
        raise ValueError(f"Unsupported assertion bound: {value!r}")

    return _walk(ast.parse(str(value).strip(), mode="eval").body)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


@dataclass
class TaskResult:
    task_name: str
    status: Literal["PASS", "FAIL", "CANCELLED"]
    duration_ms: float
    error: str | None = None
    assertions: list["AssertionResult"] = field(default_factory=list)


@dataclass
class AssertionResult:
    name: str
    expression: str
    status: AssertionStatus
    current_value: float | None = None
    expected_value: float | None = None
    # Display-only metadata (optional YAML keys on dict-form assertions) —
    # never affects pass/fail. `target` is a human-readable rendering of the
    # check itself ("50 – 150", "< 5", "≈ 200 ±5%"), so the UI can show the
    # real observed value against it instead of the raw expression.
    label: str | None = None
    description: str | None = None
    unit: str | None = None
    target: str | None = None


@dataclass
class RunResult:
    run_id: str
    scenario_name: str
    status: Literal["PASS", "FAIL", "CANCELLED"]
    tasks: list[TaskResult] = field(default_factory=list)
    assertions: list[AssertionResult] = field(default_factory=list)
    duration_ms: float = 0.0


def _extract_metric(name: str, shared_state: dict) -> float | None:
    for namespace in ("inference_results", "metrics"):
        ns = shared_state.get(namespace, {})
        if name in ns:
            return float(ns[name])
    if name in shared_state:
        try:
            return float(shared_state[name])
        except (TypeError, ValueError):
            pass
    return None


def _extract_namespaced(ref: str, shared_state: dict) -> float | None:
    """Resolve an explicit 'namespace.key' reference, e.g. 'metrics.total_requests_delta'."""
    namespace, _, key = ref.partition(".")
    if not key:
        return None
    ns = shared_state.get(namespace, {})
    if not isinstance(ns, dict) or key not in ns:
        return None
    try:
        return float(ns[key])
    except (TypeError, ValueError):
        return None


def _evaluate_match_assertion(name: str, spec: dict[str, Any], shared_state: dict) -> AssertionResult:
    """Compare two live metrics against each other within a tolerance band.

    spec: {"compare": "namespace.key", "to": "namespace.key", "tolerance_pct": <float>}
    """
    expression = f"{spec.get('compare')} ~= {spec.get('to')} (±{spec.get('tolerance_pct', 0)}%)"
    observed = _extract_namespaced(str(spec.get("compare", "")), shared_state)
    expected = _extract_namespaced(str(spec.get("to", "")), shared_state)
    if observed is None or expected is None:
        return AssertionResult(name=name, expression=expression, status="PENDING")

    tolerance_pct = float(spec.get("tolerance_pct", 0))
    allowed = tolerance_pct / 100 * max(abs(expected), 1)
    passing = abs(observed - expected) <= allowed
    return AssertionResult(
        name=name,
        expression=expression,
        status="PASSING" if passing else "FAILING",
        current_value=observed,
        expected_value=expected,
    )


_HARNESS_REF_RE = re.compile(r"\$\{harness\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\}")


def _with_live_bounds(spec: dict[str, Any], shared_state: dict) -> dict[str, Any] | None:
    """A copy of spec whose `between`/`expect` bounds have ${harness.ns.key}
    references filled from live shared_state — so a check can compare against
    a value only known at run time (e.g. the token limit read off the
    subscription being tested), not just a ${config.x} known at load time.
    None if any referenced value isn't populated yet (the assertion stays
    PENDING rather than comparing against a placeholder)."""
    missing = False

    def _sub(text: object) -> str:
        nonlocal missing

        def _one(m: re.Match) -> str:
            nonlocal missing
            ns = shared_state.get(m.group(1), {})
            if not isinstance(ns, dict) or ns.get(m.group(2)) is None:
                missing = True
                return "0"
            return str(float(ns[m.group(2)]))

        return _HARNESS_REF_RE.sub(_one, str(text))

    out = dict(spec)
    if "between" in spec:
        out["between"] = [_sub(b) for b in spec["between"]]
    if "expect" in spec:
        out["expect"] = _sub(spec["expect"])
    return None if missing else out


def _evaluate_promql_assertion(name: str, spec: dict[str, Any], shared_state: dict) -> AssertionResult:
    """Assertion backed by a live PromQL query.

    spec: {"promql": "<promql text>", "expect": "<op> <value>"} for a plain threshold,
    or {"promql": ..., "compare_to": "namespace.key", "tolerance_pct": <float>} to
    cross-check against a harness-side value the same way the match form does.

    The runner (harness/runner.py) is responsible for actually firing spec["promql"]
    each poll tick — resolving ${baseline.x}/${harness.x} template variables first —
    and writing the result into shared_state["metrics"][name]. This function only
    reads that already-fetched value; it never talks to Prometheus itself, keeping
    this module a pure, synchronous evaluator like every other assertion form.
    """
    promql = spec.get("promql", "")
    live_spec = _with_live_bounds(spec, shared_state)
    if live_spec is None:
        return AssertionResult(name=name, expression=promql, status="PENDING")
    spec = live_spec
    observed = shared_state.get("metrics", {}).get(name)
    if observed is None:
        return AssertionResult(
            name=name, expression=promql, status="PENDING", target=_promql_target(spec)
        )
    observed = float(observed)

    if "between" in spec:
        lo, hi = (_num(b) for b in spec["between"])
        return AssertionResult(
            name=name,
            expression=f"{promql} between [{_fmt(lo)}, {_fmt(hi)}]",
            status="PASSING" if lo <= observed <= hi else "FAILING",
            current_value=observed,
            target=f"{_fmt(lo)} – {_fmt(hi)}",
        )

    if "compare_to" in spec:
        expected = _extract_namespaced(str(spec.get("compare_to", "")), shared_state)
        if expected is None:
            return AssertionResult(name=name, expression=promql, status="PENDING")
        tolerance_pct = float(spec.get("tolerance_pct", 0))
        allowed = tolerance_pct / 100 * max(abs(expected), 1)
        passing = abs(observed - expected) <= allowed
        return AssertionResult(
            name=name,
            expression=f"{promql} ~= {spec.get('compare_to')} (±{tolerance_pct}%)",
            status="PASSING" if passing else "FAILING",
            current_value=observed,
            expected_value=expected,
        )

    expect = spec.get("expect")
    if expect is None:
        raise ValueError(
            f"promql assertion {name!r} needs one of 'expect', 'between' or 'compare_to'"
        )
    m = _EXPR_RE.match(expect)
    if not m:
        raise ValueError(f"Cannot parse assertion expression: {expect!r}")
    op = _OPERATORS[m.group(1)]
    passing = op(observed, _num(m.group(2)))
    return AssertionResult(
        name=name,
        expression=f"{promql} {expect}",
        status="PASSING" if passing else "FAILING",
        current_value=observed,
        target=_promql_target(spec),
    )


def _promql_target(spec: dict[str, Any]) -> str | None:
    """Human-readable rendering of a promql-form assertion's check."""
    if "between" in spec:
        try:
            lo, hi = (_num(b) for b in spec["between"])
        except (ValueError, SyntaxError, TypeError):
            return None
        return f"{_fmt(lo)} – {_fmt(hi)}"
    if "compare_to" in spec:
        return f"≈ {spec['compare_to']} ±{spec.get('tolerance_pct', 0)}%"
    m = _EXPR_RE.match(str(spec.get("expect", "")))
    if not m:
        return None
    try:
        return f"{m.group(1)} {_fmt(_num(m.group(2)))}"
    except (ValueError, SyntaxError):
        return f"{m.group(1)} {m.group(2)}"


def _with_display_metadata(result: AssertionResult, spec: dict[str, Any]) -> AssertionResult:
    result.label = spec.get("label")
    result.description = spec.get("description")
    result.unit = spec.get("unit")
    return result


def evaluate_assertion(
    name: str, expression: str | dict[str, Any], shared_state: dict
) -> AssertionResult:
    if isinstance(expression, dict):
        if "promql" in expression:
            result = _evaluate_promql_assertion(name, expression, shared_state)
        else:
            result = _evaluate_match_assertion(name, expression, shared_state)
            result.target = (
                f"≈ {expression.get('to')} ±{expression.get('tolerance_pct', 0)}%"
            )
        return _with_display_metadata(result, expression)

    value = _extract_metric(name, shared_state)
    if value is None:
        return AssertionResult(name=name, expression=expression, status="PENDING")

    m = _EXPR_RE.match(expression)
    if not m:
        raise ValueError(f"Cannot parse assertion expression: {expression!r}")

    op_str, rhs_str = m.group(1), m.group(2)
    op = _OPERATORS[op_str]
    passing = op(value, float(rhs_str))
    return AssertionResult(
        name=name,
        expression=expression,
        status="PASSING" if passing else "FAILING",
        current_value=value,
        target=f"{op_str} {rhs_str}",
    )


def evaluate_all_assertions(
    assertions: Mapping[str, Any], shared_state: dict
) -> list[AssertionResult]:
    return [evaluate_assertion(name, expr, shared_state) for name, expr in assertions.items()]


def compute_run_status(
    task_results: list[TaskResult], assertion_results: list[AssertionResult]
) -> Literal["PASS", "FAIL"]:
    if any(t.status == "FAIL" for t in task_results):
        return "FAIL"
    if any(a.status == "FAILING" for a in assertion_results):
        return "FAIL"
    return "PASS"
