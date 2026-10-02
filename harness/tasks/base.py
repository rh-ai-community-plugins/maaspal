from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from harness.result import TaskResult


@dataclass
class TaskContext:
    run_id: str
    scenario_name: str
    maas_api_url: str
    sa_token: str
    shared_state: dict
    config: dict
    assertions: dict[str, str | dict]
    emit_assertion_state: Callable[[], Awaitable[None]]
    metrics_queries: dict[str, str] = field(default_factory=dict)


class Task(ABC):
    def __init__(self, name: str, params: dict) -> None:
        self.name = name
        self.params = params

    @abstractmethod
    async def run(self, ctx: TaskContext) -> TaskResult: ...

    @abstractmethod
    async def cleanup(self, ctx: TaskContext) -> None: ...


def record_created(
    ctx: TaskContext, task: str, kind: str, name: str, *, existed: bool = False, **extra: object
) -> None:
    """Record a cluster object this run created (or patched, if it `existed`)
    for the run page's resource list and its per-resource cleanup status
    (harness/cleanup_state.py). Pass names and plain metadata only — never
    key values or tokens; this is shown in the UI."""
    ctx.shared_state.setdefault("_created", []).append(
        {
            "kind": kind,
            "name": name,
            "task": task,
            "action": "patched" if existed else "created",
            "status": "active",
            **{k: v for k, v in extra.items() if v},
        }
    )
