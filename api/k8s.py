import json
import os
import threading
import time
from pathlib import Path

IMAGE = os.environ.get("MAASPAL_IMAGE", "quay.io/rh-aiservices-bu/maaspal:latest")
NAMESPACE = os.environ.get("NAMESPACE", "maaspal")
_GLOBAL_CM = "maaspal-global-config"
# How long a run's pod gets, after stop_run() asks it to stop, before Kubernetes
# SIGKILLs it — must comfortably exceed worst-case cleanup time (sequential MaaS
# API key revocation, MaaSSubscription CR restore). Generous default; the harness
# itself typically finishes cleanup and exits well before this is ever reached.
_STOP_GRACE_PERIOD_S = int(os.environ.get("MAASPAL_STOP_GRACE_PERIOD_S", "120"))

_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_LOGS_DIR = _DATA_DIR / "logs"

# run_id → active Thread (while streaming from pod)
_streaming_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def _kube():
    from kubernetes import client as k8s, config as k8s_cfg

    try:
        k8s_cfg.load_incluster_config()
    except k8s_cfg.ConfigException:
        k8s_cfg.load_kube_config()
    return k8s


def _api_server_node(k8s: object) -> str | None:
    try:
        core = k8s.CoreV1Api()  # type: ignore[attr-defined]
        pods = core.list_namespaced_pod(
            namespace=NAMESPACE, label_selector="app=maaspal"
        )
        if pods.items:
            return pods.items[0].spec.node_name
    except Exception:
        pass
    return None


def create_job(scenario: str, run_id: str, config_overrides: dict | None = None) -> None:
    k8s = _kube()
    batch = k8s.BatchV1Api()
    node_name = _api_server_node(k8s)

    extra_env = [
        k8s.V1EnvVar(
            name="MAASPAL_CONFIG_OVERRIDES",
            value=json.dumps(config_overrides or {}),
        )
    ]

    pod_spec = k8s.V1PodSpec(
        service_account_name="maaspal",
        restart_policy="Never",
        node_name=node_name,
        termination_grace_period_seconds=_STOP_GRACE_PERIOD_S,
        containers=[
            k8s.V1Container(
                name="harness",
                image=IMAGE,
                image_pull_policy="Always",
                command=["python", "-m", "harness.main"],
                args=[
                    "--scenario",
                    f"{os.environ.get('SCENARIOS_DIR', '/app/scenarios')}/{scenario}.yaml",
                    "--run-id",
                    run_id,
                ],
                env=extra_env,
                env_from=[
                    k8s.V1EnvFromSource(
                        config_map_ref=k8s.V1ConfigMapEnvSource(name=_GLOBAL_CM)
                    )
                ],
                volume_mounts=[
                    k8s.V1VolumeMount(name="data", mount_path="/data"),
                    k8s.V1VolumeMount(name="scenarios", mount_path="/app/scenarios"),
                ],
            )
        ],
        volumes=[
            k8s.V1Volume(
                name="data",
                persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
                    claim_name="maaspal-data"
                ),
            ),
            k8s.V1Volume(
                name="scenarios",
                config_map=k8s.V1ConfigMapVolumeSource(name="maaspal-scenarios"),
            ),
        ],
    )

    safe_scenario = scenario.lower().replace("_", "-")[:12].rstrip("-")
    job_name = f"maaspal-{safe_scenario}-{run_id[:6]}"

    job = k8s.V1Job(
        metadata=k8s.V1ObjectMeta(name=job_name, labels={"maaspal-run-id": run_id}),
        spec=k8s.V1JobSpec(
            ttl_seconds_after_finished=3600,
            # Never re-run a scenario: the Job controller would otherwise
            # replace a failed — or stopped — pod with a fresh one under the
            # same run id, re-running every task and re-creating keys,
            # subscriptions and models (confirmed live: default backoffLimit 6).
            backoff_limit=0,
            template=k8s.V1PodTemplateSpec(
                metadata=k8s.V1ObjectMeta(labels={"maaspal-run-id": run_id}),
                spec=pod_spec,
            ),
        ),
    )
    batch.create_namespaced_job(namespace=NAMESPACE, body=job)

    # Kick off log capture immediately so the thread is already waiting
    # for the pod by the time the user opens the run detail page.
    ensure_log_capture(run_id)


def stop_run(run_id: str) -> bool:
    """Ask a run to stop gracefully. Returns False if no pod exists yet (e.g.
    the run is still PENDING) — caller should finalize the DB row directly in
    that case, since there's no harness process that will ever self-report.

    Suspends the run's *Job* (spec.suspend=true). Kubernetes then deletes the
    Job's pod with its own terminationGracePeriodSeconds — SIGTERM now, SIGKILL
    after _STOP_GRACE_PERIOD_S — so harness/main.py's SIGTERM handler still
    runs task cleanup, exactly as before. Unlike deleting the pod directly
    (the previous approach, ADR-016), a suspended Job never starts a
    replacement pod: confirmed live, deleting the pod made the Job controller
    re-run the whole scenario under the same run id.

    The Job is found via the pod's ownerReference, so this works for Jobs
    created before they carried the maaspal-run-id label too. Without the
    `patch jobs` RBAC grant (deploy/rbac.yaml), falls back to deleting the pod
    — and warns, since the Job may then re-run it.
    """
    k8s = _kube()
    core = k8s.CoreV1Api()
    pods = core.list_namespaced_pod(
        namespace=NAMESPACE, label_selector=f"maaspal-run-id={run_id}"
    )
    if not pods.items:
        return False
    pod = pods.items[0]
    job_name = next(
        (ref.name for ref in pod.metadata.owner_references or [] if ref.kind == "Job"), None
    )
    if job_name:
        try:
            k8s.BatchV1Api().patch_namespaced_job(
                name=job_name, namespace=NAMESPACE, body={"spec": {"suspend": True}}
            )
            print(f"[api] stop {run_id}: suspended Job {job_name}", flush=True)
            return True
        except k8s.ApiException as exc:
            print(
                f"[api] stop {run_id}: could not suspend Job {job_name} ({exc.status} {exc.reason}) — "
                "falling back to deleting the pod, which the Job may replace. Apply "
                "deploy/rbac.yaml (patch on jobs) to fix.",
                flush=True,
            )
    core.delete_namespaced_pod(
        name=pod.metadata.name,
        namespace=NAMESPACE,
        grace_period_seconds=_STOP_GRACE_PERIOD_S,
    )
    return True


def run_job_state(run_id: str) -> tuple[str | None, bool]:
    """(job state, pod still exists) for a run — state is "suspended" (Stop),
    "failed", "active", or None when no Job carries the run's label. Used to
    finalize a run whose harness died without writing a result."""
    k8s = _kube()
    selector = f"maaspal-run-id={run_id}"
    jobs = k8s.BatchV1Api().list_namespaced_job(namespace=NAMESPACE, label_selector=selector).items
    pods = k8s.CoreV1Api().list_namespaced_pod(namespace=NAMESPACE, label_selector=selector).items
    if not jobs:
        return None, bool(pods)
    job = jobs[0]
    if job.spec.suspend:
        state = "suspended"
    elif (job.status.failed or 0) > 0:
        state = "failed"
    else:
        state = "active"
    return state, bool(pods)


def delete_stopped_job(run_id: str) -> None:
    """Remove a run's suspended Job once the run is final — a suspended Job
    never completes, so ttlSecondsAfterFinished would never clean it up.
    Best-effort: a missing Job or missing RBAC is just logged."""
    try:
        k8s = _kube()
        batch = k8s.BatchV1Api()
        for job in batch.list_namespaced_job(
            namespace=NAMESPACE, label_selector=f"maaspal-run-id={run_id}"
        ).items:
            if job.spec.suspend:
                batch.delete_namespaced_job(
                    name=job.metadata.name, namespace=NAMESPACE, propagation_policy="Background"
                )
                print(f"[api] deleted stopped Job {job.metadata.name}", flush=True)
    except Exception as exc:
        print(f"[api] could not delete stopped Job for {run_id}: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Log capture: background thread writes pod stdout to disk.
# REST polling reads from disk — no SSE, no proxy buffering.
# ---------------------------------------------------------------------------

def _capture_logs(run_id: str) -> None:
    """Background thread: find the pod and poll its logs to /data/logs/{run_id}.log.

    Uses repeated read_namespaced_pod_log(follow=False) rather than the
    streaming follow=True API.  The non-follow call always returns the full log
    from the start of the container, so we can never miss early lines due to
    connection timing.  We call it every second, track how many lines we've
    already written, and append the new ones.
    """
    tmp_file = _LOGS_DIR / f"{run_id}.log.tmp"
    log_file = _LOGS_DIR / f"{run_id}.log"

    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)

        k8s = _kube()
        core = k8s.CoreV1Api()
        label = f"maaspal-run-id={run_id}"

        # Wait for the pod to appear (up to 120 s).
        pod_name: str | None = None
        for _ in range(60):
            pods = core.list_namespaced_pod(namespace=NAMESPACE, label_selector=label)
            if pods.items:
                pod_name = pods.items[0].metadata.name
                break
            time.sleep(2)

        if pod_name is None:
            print(f"[k8s] pod not found for run {run_id}", flush=True)
            return

        seen_lines = 0
        with tmp_file.open("w", encoding="utf-8") as f:
            while True:
                # Check if the pod has finished before reading logs so that
                # the log read that follows is guaranteed to include all output.
                done = False
                try:
                    pod_obj = core.read_namespaced_pod(name=pod_name, namespace=NAMESPACE)
                    phase = (pod_obj.status.phase or "") if pod_obj.status else ""
                    done = phase in ("Succeeded", "Failed")
                except Exception:
                    pass

                # Read the complete log from the beginning every iteration.
                # _preload_content=False gives us the raw urllib3 response so
                # we can decode the bytes ourselves — the default deserialiser
                # calls str() on bytes which produces a b"..." repr string.
                try:
                    response = core.read_namespaced_pod_log(
                        name=pod_name, namespace=NAMESPACE,
                        follow=False, _preload_content=False,
                    )
                    raw_bytes = response.read()
                    text = raw_bytes.decode(errors="replace") if raw_bytes else ""
                    all_lines = text.splitlines()
                    for line in all_lines[seen_lines:]:
                        f.write(line + "\n")
                        f.flush()
                    seen_lines = len(all_lines)
                except Exception as exc:
                    # Container may not have started writing yet — keep trying.
                    print(f"[k8s] log read error for {run_id}: {exc}", flush=True)

                if done:
                    break

                time.sleep(1)

    except Exception as exc:
        print(f"[k8s] log capture error for {run_id}: {exc}", flush=True)
    finally:
        # Atomically promote tmp → final log file.
        if tmp_file.exists():
            try:
                tmp_file.rename(log_file)
            except OSError:
                pass


def ensure_log_capture(run_id: str) -> None:
    """Start a log-capture thread for run_id if one is not already active."""
    log_file = _LOGS_DIR / f"{run_id}.log"
    if log_file.exists():
        return  # already complete, nothing to do

    with _threads_lock:
        thread = _streaming_threads.get(run_id)
        if thread and thread.is_alive():
            return  # already capturing
        thread = threading.Thread(target=_capture_logs, args=(run_id,), daemon=True)
        _streaming_threads[run_id] = thread
        thread.start()


def get_log_lines(run_id: str, offset: int) -> tuple[list[str], bool]:
    """Return (new_lines_since_offset, is_complete).

    Reads from the complete .log file if available, otherwise from the
    in-progress .log.tmp file.  Safe to call concurrently with _capture_logs.
    """
    log_file = _LOGS_DIR / f"{run_id}.log"
    tmp_file = _LOGS_DIR / f"{run_id}.log.tmp"

    for path, done in ((log_file, True), (tmp_file, False)):
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            all_lines = [l for l in text.splitlines() if l]
            return all_lines[offset:], done
        except OSError:
            continue

    return [], False
