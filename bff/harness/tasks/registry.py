from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.tasks.base import Task

REGISTRY: dict[str, type[Task]] = {}


def get_task_class(name: str) -> type[Task]:
    if name not in REGISTRY:
        raise KeyError(f"Unknown task: {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]


# Trigger self-registration of all known task modules
from harness.tasks import access_policy as _access_policy  # noqa: E402, F401
from harness.tasks import analysis as _analysis  # noqa: E402, F401
from harness.tasks import auth as _auth  # noqa: E402, F401
from harness.tasks import identity as _identity  # noqa: E402, F401
from harness.tasks import inference as _inference  # noqa: E402, F401
from harness.tasks import metrics as _metrics  # noqa: E402, F401
from harness.tasks import model as _model  # noqa: E402, F401
from harness.tasks import platform_health as _platform_health  # noqa: E402, F401
from harness.tasks import stubs as _stubs  # noqa: E402, F401
from harness.tasks import subscription as _subscription  # noqa: E402, F401
from harness.tasks import subscription_check as _subscription_check  # noqa: E402, F401
