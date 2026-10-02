# ADR-016: Graceful Run Stop via Pod Delete + SIGTERM Handler

## Status

Accepted

## Context

Runs had no way to be stopped once started — a user watching a long or misconfigured run (e.g. a `request_count` set far too high, see the 429-flood incident during ADR-015's rollout) had no way to end it except waiting it out. A "Stop" action needed to be added, but the harness had **zero signal handling anywhere** (confirmed via full-repo grep — no `signal.signal`, no `atexit`, no try/finally around `ScenarioRunner.run()`): every task's `cleanup()` (`for task in reversed(tasks): await task.cleanup(ctx)`, `harness/runner.py`) only ever ran after the main task loop exited on its own. A naive implementation — deleting the run's Job or pod outright — would deliver a raw SIGTERM/SIGKILL with no handler, skipping cleanup entirely and orphaning whatever the run had already provisioned (MaaS API keys via `provision_api_key`, a `MaaSSubscription` CR via `apply_rate_limit_subscription`).

## Decision

**Signal delivery: delete the run's *pod* with a grace period, not the Job, not `exec`.** `api/k8s.py:stop_run(run_id)` finds the pod by its existing `maaspal-run-id={run_id}` label (the same label `_capture_logs` already uses) and calls `core.delete_namespaced_pod(..., grace_period_seconds=_STOP_GRACE_PERIOD_S)` — exactly what `kubectl delete pod --grace-period=N` does: kubelet sends SIGTERM immediately, then SIGKILLs after `N` seconds if the container hasn't exited. Two alternatives were considered and rejected:
- **Deleting the Job object** instead of the pod would race the pod's own graceful shutdown on a timeline the API doesn't control, and would make the Job disappear from `kubectl`/the API's view before its natural terminal state is recorded — both `_capture_logs` and `api/main.py`'s `_sync_completed_runs` poller key off the *pod's* phase, not the Job's.
- **`exec`-ing a kill signal into the container** (the Kubernetes API's pod-exec equivalent of `kubectl exec`) requires a shell and a way to target the exact PID inside a possibly-minimal image, and doesn't map onto the pod's own lifecycle the way a real deletion with a grace period does.

`_STOP_GRACE_PERIOD_S` defaults to 120s (`MAASPAL_STOP_GRACE_PERIOD_S` env override) — well above Kubernetes' 30s default — sized for worst-case cleanup: `ProvisionApiKeyTask.cleanup()` issues one `DELETE` per provisioned key sequentially, and `ApplyRateLimitSubscriptionTask.cleanup()` restores or deletes a CR. `create_job()` sets this as the pod's `terminationGracePeriodSeconds` (previously unset, defaulting to Kubernetes' standard 30s). This required adding `delete` to `deploy/rbac.yaml`'s `pods` rule (previously `get`/`list`/`watch` only) — the SA's existing `delete` on `jobs` doesn't cover this, since Job-delete cascades to pods via the cluster's own garbage collector using a different identity, not the SA's RBAC.

**Harness-side: a SIGTERM handler that lets the existing task loop and cleanup logic run themselves out, not a signal-triggered fast-path.** `harness/main.py` registers `loop.add_signal_handler(signal.SIGTERM, stop_event.set)` and passes the resulting `asyncio.Event` into `ScenarioRunner(..., stop_event=stop_event)`. Inside `run()`:
- The task loop checks the event before starting each task and breaks if set.
- The in-flight `task.run(ctx)` call itself is raced against the event via `asyncio.wait({run_future, stop_waiter}, return_when=FIRST_COMPLETED)`; if the stop wins, `run_future` is cancelled and awaited (`asyncio.CancelledError` is a `BaseException` in Python ≥3.8, so it can't be accidentally swallowed by any task's existing `except Exception` blocks), producing a `TaskResult(status="CANCELLED", error="run stopped by user")`. No changes were needed inside any individual `harness/tasks/*.py` file — cancellation propagates generically, including through `send_requests`'s internal `asyncio.gather`.
- `_settle_and_evaluate`'s poll-sleep and the background metrics poller's sleep both moved from plain `asyncio.sleep(...)` to a small `_interruptible_sleep` helper (races the same sleep against `stop_event.wait()` via `asyncio.wait_for`), so a stop arriving mid-settle wakes immediately instead of waiting out the full interval.
- The existing `for task in reversed(tasks): await task.cleanup(ctx)` block runs **completely unchanged**, regardless of *why* the loop exited — cleanup already just reads whatever's in `shared_state` at that point (e.g. however many keys had been provisioned before cancellation), so no special-casing was needed there.
- Final `RunResult.status` is `"CANCELLED"` — checked once, directly against `self._stop_event.is_set()`, right before constructing the final result, rather than threading a `stopped` flag through every early-return path in `run()`. This is a deliberate simplification: once the signal handler sets the event it never unsets, so a single check at the very end is equivalent to checking at each exit point, with far less surface area for a missed case.

**API-side status: let the existing poller be the only writer of `CANCELLED`.** `POST /api/runs/{id}/stop` (`api/routes/runs.py`) calls `stop_run()`; when a pod exists, the route does **not** write `runs.status` itself — it relies on `api/main.py`'s existing `_sync_completed_runs` poller picking up the harness's own final `"CANCELLED"` from the result JSON on its normal 10s cadence, the same single-source-of-truth path every other terminal transition (`PASS`/`FAIL`) already goes through. Writing it synchronously from the stop route would risk a race against that poller overwriting it moments later with whatever the harness actually reported. The one exception: a run still `PENDING` (no pod created yet) is finalized synchronously by the route, since there's no harness process there that will ever self-report.

## Consequences

**Positive:**
- Provisioned MaaS resources (API keys, `MaaSSubscription` CRs) are cleaned up on a user-initiated stop exactly as they would be on any other run ending — no new orphan-resource risk was introduced by adding this feature.
- Zero changes needed inside any `harness/tasks/*.py` file — cancellation via `asyncio.wait`/`CancelledError` is generic across every task, present and future.
- Reuses every existing completion-detection mechanism (`_capture_logs`'s pod-phase polling, `_sync_completed_runs`'s result-JSON polling) unchanged, since a graceful stop still ends with the pod reaching a real terminal phase and the harness writing its usual final JSON.

**Negative:**
- **Known gap, accepted as matching pre-existing behavior rather than solved here**: if cleanup overruns the grace period and the container is `SIGKILL`ed, the pod still reaches a terminal phase (so `_capture_logs` still terminates correctly), but `harness/main.py` never gets to write the final result JSON — the run stays `RUNNING` in the DB forever. This is the same pre-existing gap as any other ungraceful crash (e.g. OOMKill) that skips the final write, not a new regression specific to stop; no safety-net timeout was added for it.
- A stop requested in the small window between the task loop's last iteration finishing and the process naturally exiting would still report `CANCELLED` even though the run had, in effect, already finished on its own. Accepted as a rare, harmless edge case in exchange for the single-check-at-the-end simplicity above.
- `TaskResult`/`RunResult.status` gained a third value (`CANCELLED`) alongside `PASS`/`FAIL`, touching the DB schema (a new plain-`TEXT` value, no migration needed), the live progress JSON, and every status-color mapping in the UI (`RunDetail`, `RunHistory`, `TaskProgress`, `AssertionPanel`) — a small but real surface-area increase, consistent with how `CANCELLED` needed to be threaded through as its own first-class terminal state rather than folded into `FAIL`.

**Neutral:**
- `deploy/rbac.yaml` gained `delete` on the `pods` resource (previously `get`/`list`/`watch` only) — the only RBAC change this feature required.

## Update: suspend the Job, don't delete the pod

Deleting the run's pod turned out to be unsafe. The Job had the default `backoffLimit` (6), so the Job controller replaced the deleted pod with a fresh one under the same run id. That new pod re-ran the whole scenario, re-created its cluster resources, and overwrote the run's progress files: on the run page, the cancelled run flipped back to a run starting from scratch. This was confirmed live.

`stop_run` now **suspends the Job** (`spec.suspend: true`). Kubernetes deletes the pod gracefully, with the same `terminationGracePeriodSeconds`, so the SIGTERM handler and cleanup run exactly as before, and a suspended Job never creates a replacement. Every Job also gets `backoffLimit: 0`, so a crash can't silently re-run a scenario either.

- The run's Job is found through the pod's `ownerReference`, so this also works for Jobs created before they carried the run-id label.
- It needs `patch` on `jobs` (`deploy/rbac.yaml`). Without it, `stop_run` falls back to deleting the pod and logs a warning.
- Suspended Jobs never complete, so their TTL never fires. The API deletes a stopped run's Job once it has recorded the run as CANCELLED.

## Update: a stop that never finished

Confirmed live on a stopped `load_test` run: cleanup completed, but the harness never wrote its result, the pod lingered until SIGKILL, and the run stayed `RUNNING`. The cause was the background metrics poller.
- After the stop, `_interruptible_sleep` returned without yielding, because waiting on an already-set Event never suspends.
- The poller had nothing to query, because its checks referenced values a stopped run never produces.
- Together that made a loop that starved the event loop, so it could never be cancelled.

**Fixes:**
- The sleep always yields, and the poller exits on stop.
- The pooled request workers cancel their ramp or step controller on the way out.
- As a backstop, the API finalizes a run whose Job is suspended or failed, whose pod is gone, and which never wrote a result.
