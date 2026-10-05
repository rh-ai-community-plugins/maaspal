import pytest

from harness.durations import parse_duration_s
from harness.tasks.base import TaskContext
from harness.tasks.stubs import PauseTask


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5, 5.0), ("30", 30.0), ("30s", 30.0), ("1m", 60.0), ("24h", 86400.0), ("500ms", 0.5)],
)
def test_parse_duration_s(value: object, expected: float) -> None:
    assert parse_duration_s(value) == expected


def test_parse_duration_s_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Cannot parse duration"):
        parse_duration_s("soon")


def _ctx() -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="r", scenario_name="s", maas_api_url="", sa_token="", shared_state={},
        config={}, assertions={}, emit_assertion_state=_emit,
    )


async def test_pause_accepts_window_string_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr("harness.tasks.stubs.asyncio.sleep", _fake_sleep)
    ctx = _ctx()
    await PauseTask("pause", {"duration_s": "24h", "max_s": 3, "reason": "window reset"}).run(ctx)
    assert sum(slept) == pytest.approx(3)
    assert "capped at 3s (requested 86400s)" in ctx.shared_state["task_summary"]
    assert ctx.shared_state["task_progress"] == {"current": 3, "total": 3}


async def test_pause_adds_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr("harness.tasks.stubs.asyncio.sleep", _fake_sleep)
    await PauseTask("pause", {"duration_s": "2s", "buffer_s": 1}).run(_ctx())
    assert sum(slept) == pytest.approx(3)
