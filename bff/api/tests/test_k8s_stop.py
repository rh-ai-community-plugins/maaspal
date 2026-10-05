"""Stop must never let the Job controller re-run a scenario (confirmed live:
deleting the run's pod made the Job start a fresh pod under the same run id,
re-running every task and re-creating cluster resources)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kubernetes import client as k8s_client

from api import k8s as k8s_module


@pytest.fixture
def fake_k8s(monkeypatch: pytest.MonkeyPatch):
    core = MagicMock()
    batch = MagicMock()
    fake = SimpleNamespace(
        CoreV1Api=lambda: core,
        BatchV1Api=lambda: batch,
        ApiException=k8s_client.ApiException,
        **{name: getattr(k8s_client, name) for name in dir(k8s_client) if name.startswith("V1")},
    )
    monkeypatch.setattr(k8s_module, "_kube", lambda: fake)
    monkeypatch.setattr(k8s_module, "ensure_log_capture", lambda run_id: None)
    return core, batch


def _pod(owner_job: str | None = "maaspal-load-test-abc123"):
    refs = [SimpleNamespace(kind="Job", name=owner_job)] if owner_job else []
    return SimpleNamespace(metadata=SimpleNamespace(name="pod-1", owner_references=refs))


def test_stop_suspends_the_job_instead_of_deleting_the_pod(fake_k8s) -> None:
    core, batch = fake_k8s
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod()])

    assert k8s_module.stop_run("run-1") is True
    batch.patch_namespaced_job.assert_called_once_with(
        name="maaspal-load-test-abc123", namespace=k8s_module.NAMESPACE, body={"spec": {"suspend": True}}
    )
    core.delete_namespaced_pod.assert_not_called()


def test_stop_falls_back_to_pod_delete_without_patch_rbac(fake_k8s) -> None:
    core, batch = fake_k8s
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod()])
    batch.patch_namespaced_job.side_effect = k8s_client.ApiException(status=403, reason="Forbidden")

    assert k8s_module.stop_run("run-1") is True
    core.delete_namespaced_pod.assert_called_once()


def test_stop_without_a_pod_reports_false(fake_k8s) -> None:
    core, _ = fake_k8s
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    assert k8s_module.stop_run("run-1") is False


def test_jobs_never_retry_and_carry_the_run_id_label(fake_k8s) -> None:
    core, batch = fake_k8s
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    k8s_module.create_job("load_test", "run-123456")
    job = batch.create_namespaced_job.call_args.kwargs["body"]
    assert job.spec.backoff_limit == 0
    assert job.metadata.labels == {"maaspal-run-id": "run-123456"}


def test_delete_stopped_job_only_removes_suspended_jobs(fake_k8s) -> None:
    _, batch = fake_k8s
    batch.list_namespaced_job.return_value = SimpleNamespace(items=[
        SimpleNamespace(metadata=SimpleNamespace(name="stopped"), spec=SimpleNamespace(suspend=True)),
        SimpleNamespace(metadata=SimpleNamespace(name="finished"), spec=SimpleNamespace(suspend=None)),
    ])
    k8s_module.delete_stopped_job("run-1")
    batch.delete_namespaced_job.assert_called_once_with(
        name="stopped", namespace=k8s_module.NAMESPACE, propagation_policy="Background"
    )
