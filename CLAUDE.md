# MaaS:PAL - RHOAI MaaS Testing Harness

## Context

A testing harness for Red Hat OpenShift AI (RHOAI) Models as a Service (MaaS). Validates existing RHOAI environments by running test scenarios against live endpoints. The harness user will typically be an admin, but uses RHOAI's dedicated endpoints and functionality as intended (not bypassing RHOAI's own access controls — acting through RHOAI's user-facing APIs, not raw cluster admin operations).

Tests are composed of atomic **tasks** (e.g., provision API key, send inference requests) grouped into **scenarios** (user-selectable flows defined in YAML). The harness runs in-cluster as Kubernetes Jobs, orchestrated by a web UI accessed via an OpenShift Route.

## Architecture Overview

```
                  Browser UI (React + TypeScript + PatternFly 5)
                  - Pick scenario, override config params, start run
                  - Live log polling (REST, 1s interval) with smart scroll
                  - Live assertion status panel (2s poll, independent of logs)
                  - Task progress pipeline (2s poll)
                  - Results history + per-run detail view; URL hash routing (#run/<id>)
                          |
                  FastAPI Backend (Deployment)
                  - Serve UI static files
                  - GET  /api/scenarios
                  - POST /api/runs  (accepts config_overrides)
                  - GET  /api/runs, /api/runs/{id}
                  - POST /api/runs/{id}/stop                  (graceful stop, see below)
                  - GET  /api/runs/{id}/logs/lines?offset=N  (REST poll)
                  - GET  /api/runs/{id}/assertions            (reads PVC file)
                  - GET  /api/runs/{id}/progress              (reads PVC file)
                  - GET  /api/runs/{id}/config                (reads PVC file, YAML text)
                  - Creates K8s Jobs, captures pod logs to PVC
                  - SQLite run history on PVC
                          |
                  Kubernetes Job (one per run)
                  - Reads global ConfigMap + scenario YAML
                  - Executes tasks in order using SA token
                  - Background MaaS metrics poller (every 5s, if MAAS_METRICS_URL set)
                  - SIGTERM handler: breaks the task loop, still runs cleanup
                  - Runs cleanup at end (pass, fail, or stopped)
                  - Writes assertions + progress + config snapshot to PVC in real time
```

## MaaS API (key facts)

- **Base URL**: `https://maas.${CLUSTER_DOMAIN}` (cluster domain from `kubectl get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}'`)
- **Auth**: OpenShift bearer token — SA token auto-mounted at `/var/run/secrets/kubernetes.io/serviceaccount/token`
- **Key creation**: `POST /maas-api/v1/api-keys` → returns `{ id, key (sk-oai-...), name, subscription, expiresAt }`
- **Key search**: `POST /maas-api/v1/api-keys/search`
- **Key revoke**: `DELETE /maas-api/v1/api-keys/{id}` or `POST /maas-api/v1/api-keys/bulk-revoke`
- **Model discovery**: `GET /v1/models` → `{ data: [{ id, url }] }`
- **Inference**: `POST ${MODEL_URL}/v1/chat/completions` (OpenAI-compatible, auth with `sk-oai-*` key)
- **Subscriptions**: No REST API for creating/modifying subscriptions. Rate limits are configured via the `MaaSSubscription` CRD (`maas.opendatahub.io/v1alpha1`). Use `kubernetes.client.CustomObjectsApi` to apply/patch these objects. The MaaS REST API is for api-key lifecycle only.

## Repository Structure

```
maaspal/
├── harness/                    # Test runner (K8s Job entrypoint)
│   ├── __init__.py
│   ├── main.py                 # Job entrypoint: loads kube client config (ADR-009 update — required for every CR-based task), registers SIGTERM handler, run scenario, cleanup
│   ├── runner.py               # ScenarioRunner: executes tasks, records results, handles graceful stop
│   ├── result.py               # RunResult/TaskResult dataclasses (status incl. CANCELLED, duration_ms) + assertion evaluation
│   ├── config.py               # Config loader: merges global ConfigMap + scenario YAML
│   ├── metrics_client.py       # fetch_metrics: shared Thanos Querier client (background poller + check_maas_metrics both use this)
│   ├── durations.py            # parse_duration_s: "30s"/"1m"/"24h" window strings → seconds
│   └── tasks/
│       ├── __init__.py
│       ├── base.py             # Task ABC: run(ctx) -> TaskResult, cleanup(ctx) -> None
│       ├── auth.py             # provision_api_key, revoke_api_keys, verify_api_key_search — all REST-only (uses SA token -> MaaS API)
│       ├── identity.py         # create_user, provision_keys_for_users (ADR-023): mint throwaway ServiceAccount identities + per-user API keys
│       ├── inference.py        # send_requests: concurrent OpenAI-compat load (url/token/key_index overridable, result_key namespacing)
│       ├── metrics.py          # check_maas_metrics: read MaaS metrics (total_requests, total_tokens, etc.), log summary, store in shared_state
│       ├── subscription.py     # apply_rate_limit_subscription, apply_priority_test_subscriptions (ADR-021): create/patch MaaSSubscription CR(s)
│       ├── access_policy.py    # apply_auth_policy: create/patch MaaSAuthPolicy CR (gateway-access half of the two-layer access model, ADR-018)
│       ├── platform_health.py  # check_platform_health (REST smoke) + check_model_health (ADR-022/025): read-only model wiring checks, one or all models
│       ├── subscription_check.py # verify_subscription_models (ADR-025): per-model reachability + limit probe for one existing subscription
│       └── registry.py         # task name -> class mapping for YAML resolution
│
├── scenarios/                  # YAML scenario definitions (mounted as ConfigMap) — question-first titles/metadata, ADR-025
│   ├── smoke_test.yaml                       # Quick check: is MaaS working end to end?
│   ├── verify_subscription.yaml              # Quick check: does my subscription work for every model it covers?
│   ├── verify_subscription_rate_limit.yaml   # Rate limits: existing or temporary subscription (`mode`)
│   ├── keys_share_user_budget.yaml           # Rate limits: do all of one user's keys share one budget?
│   ├── rate_limit_window_recovery.yaml       # Rate limits: does access come back after the window?
│   ├── subscription_auto_selection.yaml      # Rate limits: priority-based auto-selection (ADR-021)
│   ├── rate_limit_per_user_or_shared.yaml    # Rate limits: per-user vs pooled, user states expectation (ADR-023)
│   ├── denied_without_auth_policy.yaml       # Access control: fail-closed without a MaaSAuthPolicy (ADR-018)
│   ├── api_key_lifecycle.yaml                # API keys (ADR-019)
│   ├── usage_metrics_accuracy.yaml           # Usage metrics: MaaS counters vs harness (ADR-014/015)
│   ├── load_test.yaml                        # Performance: 1..N keys
│   ├── gateway_overhead.yaml                 # Performance: direct vs through-MaaS latency
│   ├── multi_model_load.yaml                 # Performance: N models / M subscriptions / K keys (ADR-024)
│   └── model_config_health.yaml              # Diagnostics: read-only model wiring checks, one or all models (ADR-022)
│
├── api/                        # FastAPI backend
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, mounts static UI
│   ├── db.py                   # SQLite setup (aiosqlite)
│   ├── k8s.py                  # Create Jobs; stop_run() deletes a run's pod with a grace period; background thread log capture to PVC; REST log reading
│   └── routes/
│       ├── scenarios.py        # GET /api/scenarios (includes config defaults from YAML)
│       ├── runs.py             # POST /api/runs (config_overrides), GET /api/runs, GET /api/runs/{id}, POST /api/runs/{id}/stop
│       ├── logs.py             # GET /api/runs/{id}/logs/lines?offset=N  (REST poll, no SSE)
│       ├── assertions.py       # GET /api/runs/{id}/assertions  (reads /data/results/<id>-assertions.json)
│       ├── progress.py         # GET /api/runs/{id}/progress    (reads /data/results/<id>-progress.json)
│       └── config.py           # GET /api/runs/{id}/config      (reads /data/results/<id>-config.json, serves as YAML text)
│
├── ui/                         # Frontend (React + TypeScript + PatternFly 5; built to ui/dist/)
│   ├── src/
│   │   ├── App.tsx             # Top-level: URL hash routing (#run/<id>), pushState/popstate, MaaS:PAL masthead
│   │   ├── monacoSetup.ts      # Self-hosted Monaco config (no CDN) + trimmed to YAML-only — see RunSettingsModal below
│   │   ├── api/client.ts       # Typed fetch wrappers for all backend API routes
│   │   ├── styles/theme.css    # MaaS:PAL/God of War dark theme: dark header, red accents, card styles
│   │   └── components/
│   │       ├── ScenarioList.tsx    # Compact scenario cards with Run button; error/retry state
│   │       ├── RunTrigger.tsx      # Config override editor (pre-filled from YAML defaults) + launch modal
│   │       ├── LogStream.tsx       # REST poll consumer (1s interval), smart scroll, "N new lines" badge
│   │       ├── AssertionPanel.tsx  # Live assertion cards (Passing/Failing/Pending) with value + expression
│   │       ├── TaskProgress.tsx    # Horizontal task pipeline chips (PENDING/RUNNING/DONE/FAIL/CANCELLED) with progress bars + live/frozen duration
│   │       ├── RunHistory.tsx      # PatternFly Table of past runs; color-coded status badges; Duration column; Stop action; 3s poll while active
│   │       ├── RunDetail.tsx       # Per-run detail page: metadata bar (live elapsed time, View Settings, Stop), task pipeline, logs + assertions grid
│   │       └── RunSettingsModal.tsx # Read-only Monaco YAML view of a run's settings (GET /api/runs/{id}/config) — mirrors OpenShift console's own "View YAML"; lazy-loaded (React.lazy)
│   ├── package.json
│   └── tsconfig.json
│
├── deploy/                     # OpenShift/K8s manifests
│   ├── serviceaccount.yaml     # SA with rhoai-admin + job/pod + maassubscriptions RBAC
│   ├── rbac.yaml               # Role + RoleBinding
│   ├── rbac-monitoring.yaml    # ClusterRoleBinding: SA -> cluster-monitoring-view (Thanos Querier access)
│   ├── rbac-user-provisioning.yaml  # ADR-023: create/delete ServiceAccounts + mint serviceaccounts/token, maaspal namespace only — more sensitive than any other grant
│   ├── pvc.yaml                # PVC for SQLite DB + run results
│   ├── configmap-global.yaml   # Global cluster config (MAAS_API_URL, etc.)
│   ├── deployment.yaml         # API server Deployment
│   ├── service.yaml            # ClusterIP Service
│   └── route.yaml              # OpenShift Route (TLS edge termination)
│
├── docs/
│   ├── architecture/
│   │   ├── adrs/                          # Architecture Decision Records (ADR-001 to ADR-022)
│   │   ├── maas-metrics-reference.md      # Full catalog of MaaS/RHOAI metrics found during live-cluster research (maas-api, Limitador, vLLM, Istio, Authorino) — not just the two MaaS:PAL uses
│   │   └── empirical-verification-checklist.md  # Living catalog of UI/CR claims worth checking against real gateway behavior — Verified/Partial/Gap per area, ADR-018
│   └── project/
│       └── implementation-plan.md  # Phased implementation plan
│
├── Dockerfile                  # Multi-stage: Node (UI build) → Python (API + harness)
├── Makefile                    # build, push, deploy, dev, test, lint
├── kustomization.yaml          # oc apply -k . — deploy/ resources + generates maaspal-scenarios ConfigMap from scenarios/*.yaml (configMapGenerator; root-level so kustomize's file-load restriction allows referencing both deploy/ and scenarios/)
├── pyproject.toml              # Python deps: fastapi, uvicorn, kubernetes, aiosqlite, httpx, openai
└── README.md
```

## Key Design Decisions

### Single Container Image
Both the API server and the harness job use the same image, different entrypoints:
- API server: `uvicorn api.main:app`
- Job runner: `python -m harness.main --scenario <name> --run-id <uuid>`

### Task Model
```python
class Task(ABC):
    async def run(self, ctx: TaskContext) -> TaskResult: ...
    async def cleanup(self, ctx: TaskContext) -> None: ...
```
- `TaskContext` carries: `maas_api_url`, `sa_token`, `shared_state: dict`, resolved config, and an `emit_assertion_state()` helper
- `shared_state` is how tasks pass data forward — e.g. `provision_api_key` writes created key IDs into `shared_state["api_keys"]`; the cleanup reads and deletes them all
- Tasks report within-task progress by writing `shared_state["task_progress"] = {"current": N, "total": M}` before calling `emit_assertion_state()`. The runner snapshots and clears this once the task is *fully* done — including settling any metrics-dependent per-task assertions (see Background Metrics Polling below) — not the instant `task.run()` returns, so the UI's progress bar stays visible for the whole time a task with such assertions is settling, not just up to when its own work finished.
- `emit_assertion_state()` is called by tasks after every atomic operation that produces metric data (e.g. after each inference request in `send_requests`, after each key in `provision_api_key`). It re-evaluates all assertions against the current `shared_state` and writes the result to `/data/results/<run-id>-assertions.json` (PVC file, NOT stdout). Emission is debounced (at most every 100ms). It also writes the current task progress to `/data/results/<run-id>-progress.json`.
- **Cleanup runs after ALL tasks complete (or fail) — not per-task.** This is intentional: lets you observe the effect of many accumulated keys/resources before cleanup

### Tiered Config (three-level merge, lowest → highest precedence)

1. **Global ConfigMap** (`configmap-global.yaml`): cluster-level defaults, injected as env vars into every Job
   ```yaml
   MAAS_API_URL: "https://maas.apps.mycluster.example.com"
   DEFAULT_MODEL: "granite-3-8b-instruct"
   DEFAULT_SUBSCRIPTION: ""   # empty = auto-select highest priority
   ```

2. **Scenario YAML** `config:` section: per-scenario params (override globals where they overlap)
   ```yaml
   config:
     request_count: 1000
     concurrency: 10
     prompt: "Summarize this in one sentence."
   ```

3. **`MAASPAL_CONFIG_OVERRIDES`** env var: JSON dict injected into the Job by the API server, carrying values the user edited in the UI before launching the run. Applied at highest precedence — overrides both global ConfigMap and scenario YAML defaults.
   ```json
   {"request_count": 50, "concurrency": 2}
   ```
   `harness/config.py` reads this via `json.loads(os.environ.get("MAASPAL_CONFIG_OVERRIDES", "{}"))`. The UI pre-fills the editor with YAML `config:` defaults so users see reasonable starting values.

### Scenario YAML Format
```yaml
name: load_test
description: "Baseline load test — one key, N requests through MaaS"
category: "Load Testing"  # optional (ADR-020) — omit it and the scenario lands in the UI's "Custom" bucket
config:
  request_count: 100
  concurrency: 5
  prompt: "Hello, world!"
tasks:
  - name: provision_api_key
    params:
      key_name: "maaspal-load-key"
  - name: send_requests
    params:
      count: "${config.request_count}"
      concurrency: "${config.concurrency}"
      prompt: "${config.prompt}"
      key_pool: true
assertions:
  error_rate_pct: "< 5"
  p99_latency_ms: "< 10000"
cleanup: automatic
```
`check_maas_metrics` is no longer a task step — MaaS metrics are polled continuously in the background by `ScenarioRunner` (see below).

**Display/launch metadata (ADR-025, all optional, never read by the harness)**: `title` (the question shown in the UI instead of `name`), `summary`, `kind: verify|explore` (verify may only create API keys — enforced by `harness/tests/test_scenarios.py`), `mutates`, `needs_rbac`, `est_duration`, `order`, `requires` (config keys that gate **Launch**), `inputs` (per-config-key `label`/`help`/`advanced`/`choices`/`show_if`/`placeholder`/`from_subscription: limit|window`/`from_model: http_route` — `config:` itself stays flat), `plan_template` ("What this run will do", `${config.x}`/`${model}`/`${subscription}` substituted live in the launch form), `previous_names` (old ids so run history resolves renamed scenarios), and `verdict: {pass, fail}` sentence templates (`${config.x}` at load, `${harness.ns.key}` at run end). A task may carry `when: {config_key: value}` — it only runs when the config matches (`harness/config.py:_when_matches`), giving one scenario a mode switch instead of duplicate files.

### Assertions
- Evaluated continuously — after every atomic operation that produces new metric data (e.g. after each inference request in `send_requests`, after each key creation in `provision_api_key`). Result is written to `/data/results/<run-id>-assertions.json` on the PVC. **Not parsed from pod logs** — the log stream is plain text only.
- Frontend polls `GET /api/runs/{id}/assertions` every 2s independently of the log poll.
- Simple form: `metric_name: "<operator> <value>"` (operators: `<`, `>`, `<=`, `>=`, `==`)
- Match form (MaaS-vs-harness cross-check, see ADR-014): compares two live metrics to each other within a tolerance band instead of against a constant:
  ```yaml
  assertions:
    maas_requests_match:
      compare: metrics.total_requests_delta
      to: inference_results.total_requests
      tolerance_pct: 5
  ```
  `compare`/`to` are explicit `namespace.key` references. PASSING if `abs(observed - expected) <= tolerance_pct/100 * max(abs(expected), 1)`; PENDING if either side isn't populated yet.
- **PromQL form** (ADR-015): the MaaS-side value comes from a live PromQL query instead of a `shared_state` lookup, letting a scenario author write and see the actual metric check in the YAML instead of it being split across the global ConfigMap and two `result.py` code paths:
  ```yaml
  assertions:
    maas_requests_match:
      promql: >-
        abs(
          (sum(authorized_calls{limitador_namespace="${config.limitador_namespace}"}) - ${baseline.total_requests})
          - ${harness.inference_results.total_requests}
        )
        <= bool (${config.metrics_tolerance_pct} / 100 * clamp_min(${harness.inference_results.total_requests}, 1))
      expect: "== 1"        # or: compare_to: inference_results.total_requests / tolerance_pct: 5
  ```
  `${baseline.<name>}` resolves once (right after the pre-run baseline snapshot) to a value from the scenario's `metrics_queries:` block (see Background Metrics Polling below). `${harness.<namespace>.<key>}` resolves fresh on every poll tick from live `shared_state` (e.g. `inference_results.total_requests`) — if that value isn't populated yet, that tick's query is skipped and the assertion stays PENDING. Either `expect: "<op> <value>"` (same grammar as the simple form) or `compare_to`/`tolerance_pct` (same tolerance-band math as the match form) reads the already-fetched result; the query itself is fired by the runner, `harness/result.py` stays a pure synchronous evaluator. `compare`/`to` (match form) remains fully supported — `promql` is additive, not a replacement.
  **Does not solve run-scoping**: `${baseline.x}`/`${harness.x}` are numeric literal substitutions, not Prometheus labels — a `promql` assertion still can't distinguish this run's traffic from a concurrent run/caller hitting the same model route any better than the match form can (see the scoping caveat under Background Metrics Polling below).
- Available metrics from `send_requests` (via `shared_state["inference_results"]`): `error_rate_pct`, `p50/p95/p99_latency_ms`, `throughput_rps`, `total_requests`, `success_count`, `fail_count`, `rate_limited_count` (429s), `unauthorized_count` (401/403s — both caught via `openai.APIStatusError.status_code`, ADR-018, so a scenario can assert on *why* requests failed, not just whether), `total_tokens_sent`, `prompt_tokens_sent`, `completion_tokens_sent`, `first_rate_limited_at_tokens` (ADR-024 — cumulative `total_tokens_sent` at the moment of the *first* 429; **absent**, not `0`, until a 429 actually happens, so a referencing assertion stays honestly PENDING instead of reading a false zero — see `verify_subscription_rate_limit.yaml`/`rate_limit_per_user_or_shared.yaml`)
- Available metrics from background MaaS metrics poller (via `shared_state["metrics"]`): raw values as reported by the scenario's `metrics_queries:` block (e.g. `total_requests`, `total_tokens`), plus `{name}_delta` for each (value minus the run's baseline snapshot — see Background Metrics Polling below), plus one entry per `promql`-form assertion (keyed by assertion name). Requires `MAAS_METRICS_URL` in the global ConfigMap and at least one of `metrics_queries:`/a `promql`-form assertion in the scenario.
- Available metrics from `provision_api_key`/`verify_api_key_search` (ADR-019, ADR-021): `shared_state["key_provision_checks"]` (`total_keys`, `name_echo_match_count`, `subscription_checked_count`, `subscription_echo_match_count`, `expected_subscription_checked_count`, `expected_subscription_match_count`, `expires_at_present_count`) and `shared_state["search_check"]` (`found_count`, `expected_count`) — referenced the same way as `inference_results`/`metrics`, e.g. `${harness.key_provision_checks.name_echo_match_count}`. These are entirely REST-derived (no Prometheus/CR involved) but still routed through the `promql` pass-through form for the same parsing-path-coverage reason as every other harness-side-only metric in this codebase.
- Available metrics from `check_platform_health` (ADR-022): `shared_state["rate_limit_policy_status"]` (`found`, `accepted`, `enforced`), `["gateway_status"]` (`programmed`), `["http_route_status"]` (`found`, `owner_ref_matches`) — same referencing convention, e.g. `${harness.gateway_status.programmed}`. Read-only CR-derived flags, not Prometheus metrics.
- **Display fields (ADR-025)** on any dict-form assertion: `label`, `description`, `unit` (shown on the card instead of the raw name/expression — the substituted PromQL moves behind a "Details" toggle), plus a computed human-readable `target` ("100 – 200", "< 5"). `between: [lo, hi]` is a range form for promql-form assertions so a card shows the real value (e.g. 115 tokens) rather than a derived difference; bounds may be simple arithmetic left over from `${config.x}` substitution (`"${config.token_limit} + 100"`), parsed by an AST walker in `harness/result.py`, never `eval`.
- A PENDING assertion never fails a run, so a check whose value only appears on some outcome (e.g. `tokens_before_first_429` never appears if nothing was throttled) is always paired with an always-populated guard (e.g. `rate_limited_count > 0`).
- Run is PASS only if all assertions pass (or no assertions defined)

### Results Storage
- SQLite on PVC at `/data/maaspal.db` (tables: `runs`, `task_results`; `runs` has `config_overrides TEXT` and `duration_ms REAL` columns — `task_results` is unused dead schema, per-task data lives in the progress JSON instead)
- Run results JSON: `/data/results/<run-id>.json` — includes `RunResult.duration_ms` (total run time, `time.monotonic()`-based) and each task's `duration_ms`; `status` can be `PASS`/`FAIL`/`CANCELLED`
- Live assertion state: `/data/results/<run-id>-assertions.json` — written by `emit_assertion_state()`, served by `GET /api/runs/{id}/assertions`
- Live task progress: `/data/results/<run-id>-progress.json` — written by `_write_progress()` at task start/end and on each `emit()`, served by `GET /api/runs/{id}/progress`. Also carries the run page's narration (ADR-025): per-task `summary` (tasks set `shared_state["task_summary"]`), `traffic` (one entry per `send_requests`-family `result_key`: summary counters, a timeline downsampled to ≤600 points that always keeps status transitions, and the scenario's `token_limit` as the chart's reference line), `resources` (what the run created — names only, never key values or tokens), `tables` (rows a task publishes via `shared_state["_tables"][title]`), and the final `verdict`. Includes a top-level `run_started_at` (wall-clock ISO timestamp) and, per task, `duration_ms` once completed or `started_at` while `RUNNING` — the frontend computes/ticks elapsed time client-side from these rather than the backend pushing a live-updating number.
- Run config snapshot: `/data/results/<run-id>-config.json` — a **scenario-YAML-shaped** snapshot (`name`/`description`/`config`/`metrics_queries`/`tasks`/`assertions`/`cleanup`, built by `_scenario_settings_snapshot()`), meant to be pasted directly into a new `scenarios/*.yaml` file to reproduce the run exactly, not just inspected. `config:` merges the scenario's own declared keys (defaults + any launch-time overrides actually applied) with a small curated set of cluster-level settings (`MAAS_API_URL`, `MAAS_METRICS_URL`, `DEFAULT_MODEL`, `DEFAULT_SUBSCRIPTION`) — deliberately narrower than the raw `_resolved_config` (which is a merge of the *entire* process environment, per `harness/config.py:load_scenario`) so container plumbing (`PATH`, `HOSTNAME`, `KUBERNETES_*`, ...) never shows up. Any dict key matching `(^|_)(token|secret|password)($|_)` at *any* nesting depth (top-level config, or inside a task's resolved `params`) is redacted to `***REDACTED***` — deliberately excludes "key" as a bare substring, since this app's whole domain is provisioning MaaS API *keys* and that false-positived hard on entirely non-sensitive fields (`key_name`, `key_pool`, `total_tokens`, `maas_tokens_match` — the last one being a whole assertion, not just a leaf value, confirmed live). `token_limit`/`token_window` (ADR-024) are a narrower false positive of the same shape — "token" is a complete word in them too, but they're an LLM token budget/time window, not a credential — fixed via an exact-name exception set rather than a pattern change (unlike "key", "token" can't be excluded as a blanket substring without also un-redacting real credentials like `target_token`/`sa_token`). Written once, before the task loop starts (`ScenarioRunner.run()`), so it's viewable from the moment a run begins — served as YAML text (`sort_keys=False`, preserving scenario-file key order) by `GET /api/runs/{id}/config`. Rendered in the UI (`RunSettingsModal.tsx`) via PatternFly's `CodeEditor` (Monaco) in read-only mode — the same component family OpenShift console itself uses for "View YAML" — with built-in copy/download buttons, YAML syntax highlighting, and line numbers, so the output can be copied straight into a new scenario file. See Frontend Monaco Setup below for why it's self-hosted rather than CDN-loaded.
- Pod logs: `/data/logs/<run-id>.log` (final) or `.log.tmp` (in-progress) — see Log Streaming below

### Log Streaming (REST polling — no SSE)
SSE was removed because HAProxy (OpenShift edge-terminated Routes) buffers response bodies until the connection closes, making live streaming impossible without cluster-level proxy config changes.

Current approach:
- `create_job` immediately starts a background daemon thread (`_capture_logs`) so it is already waiting for the pod before the user opens the run detail page.
- `_capture_logs` waits up to 120 s for the Job pod to appear (polling every 2 s). Once found, it polls `read_namespaced_pod_log(follow=False, _preload_content=False)` every 1 s. Each call returns the complete log from the start; the thread tracks `seen_lines` and appends only new lines to `.log.tmp`. Using `_preload_content=False` gives a raw urllib3 response that is decoded manually — the default deserializer calls `str()` on bytes, producing `b"..."` repr strings.
- When the pod phase is `Succeeded` or `Failed`, the thread does one final read then atomically renames `.log.tmp` → `.log`. The `.log` file signals "done" to readers.
- `get_log_lines(run_id, offset)` reads from `.log` (done=True) if it exists, otherwise `.log.tmp` (done=False). Returns `(new_lines[offset:], done)`.
- The frontend polls `/api/runs/{id}/logs/lines?offset=N` every 1 s, accumulates lines. Log lines are plain text only — no JSON assertion events are parsed from the log stream (assertions use their own endpoint). Polling stops when `done=true`.
- The log view uses **smart scroll**: auto-scrolls when the user is at the bottom; if scrolled up, shows a "↓ N new lines" badge that jumps back to the bottom on click.
- Log files survive pod deletion (TTL cleanup, OCP GC) since they live on the shared PVC.

### Cleanup
- Every task's `cleanup()` runs after the full scenario regardless of pass/fail
- Cleanup failures are logged but do not mark the run as failed
- No per-run K8s Secrets needed — SA token is auto-mounted; MaaS API keys are created and deleted by harness tasks themselves
- **Metrics pipeline data is not cleaned up.** MaaS metrics polling is read-only; any request traces or counters written to the RHOAI metrics pipeline during a run are intentionally left in place. Cleaning up historical metrics data is deferred to future work.
- Primary cleanup targets: MaaS API keys (bulk-revoked via `/maas-api/v1/api-keys/bulk-revoke`) and `MaaSSubscription` CRs (restored or deleted via Kubernetes API)

### Stopping a Run (graceful)

See ADR-016 for the full reasoning behind pod-delete-with-grace-period vs. Job-delete vs. `exec`, and why the harness-side signal handler defers to the existing task loop/cleanup rather than a fast-path.

`POST /api/runs/{id}/stop` asks a run to stop — not a raw kill, since the harness has task cleanup (MaaS API key revocation, `MaaSSubscription` CR restoration) that must still run.

- `api/k8s.py:stop_run(run_id)` finds the run's pod by its existing `maaspal-run-id={run_id}` label and calls `core.delete_namespaced_pod(..., grace_period_seconds=_STOP_GRACE_PERIOD_S)` — the same mechanism `kubectl delete pod --grace-period=N` uses. It deletes the **pod**, not the Job — deleting the Job instead would race the pod's own graceful shutdown and confuse `_capture_logs`/`_sync_completed_runs`, both of which key off the pod's phase. `_STOP_GRACE_PERIOD_S` defaults to 120s (`MAASPAL_STOP_GRACE_PERIOD_S` env override), well above Kubernetes' 30s default, sized for worst-case cleanup (sequential key revocation, subscription restore); `create_job()` sets this as the pod's `terminationGracePeriodSeconds`.
- `harness/main.py` registers a `SIGTERM` handler (`loop.add_signal_handler`) that sets an `asyncio.Event`, passed into `ScenarioRunner(..., stop_event=...)`. Without this, the harness has no signal handling at all and a SIGTERM would just kill the process outright, skipping cleanup entirely.
- Inside `ScenarioRunner.run()`: the task loop checks the event before starting each task; the in-flight `task.run(ctx)` itself is raced against the event via `asyncio.wait(..., return_when=FIRST_COMPLETED)` and cancelled if the stop wins, producing a `TaskResult(status="CANCELLED", error="run stopped by user")`; `_settle_and_evaluate`'s poll-sleep and the background metrics poller's sleep both use a small `_interruptible_sleep` helper so a stop wakes them immediately instead of waiting out the interval. The existing `for task in reversed(tasks): await task.cleanup(ctx)` block runs completely unchanged regardless of *why* the loop exited — cleanup already just reads whatever's in `shared_state` at that point (e.g. however many keys were provisioned before cancellation).
- Final `RunResult.status` is `"CANCELLED"` whenever the stop event ends up set, checked once at the very end of `run()` rather than threaded through every early-return path.
- The `POST /api/runs/{id}/stop` route itself does **not** write the DB row when a pod exists (`stop_run` returns `True`) — it relies on the existing `_sync_completed_runs` poller (`api/main.py`) picking up the harness's own final `"CANCELLED"` status from the result JSON on its normal 10s cadence, avoiding a race between the endpoint and the poller. It's a 404 if the run doesn't exist, a 409 if it's already terminal (not `PENDING`/`RUNNING`). Only the no-pod-yet (`PENDING`) case is finalized synchronously by the route, since there's no harness process there to ever self-report.
- **Known gap, matching existing crash behavior**: if cleanup overruns the grace period and the container is `SIGKILL`ed, the pod still reaches a terminal phase (so `_capture_logs` still terminates correctly), but `harness/main.py` never gets to write the final result JSON — the run stays `RUNNING` in the DB forever. This is the same pre-existing gap as any other ungraceful crash (e.g. OOMKill), not something specific to stop; no safety-net timeout was added for it.

### SA Permissions Required
- `rhoai-admin` ClusterRole (or equivalent) — to call MaaS API
- `create`, `get`, `list`, `watch`, `delete` on `jobs` in the harness namespace
- `get`, `list`, `watch`, `delete` on `pods` — `delete` is for `POST /api/runs/{id}/stop` (`api/k8s.py:stop_run`, deletes the run's *pod* with a grace period rather than the Job, so `_capture_logs`/`_sync_completed_runs`'s existing pod-phase-based completion detection keeps working unchanged)
- `get` on `pods/log`
- `get`, `create`, `patch`, `delete` on `maassubscriptions` and `maasauthpolicies` (`maas.opendatahub.io/v1alpha1`) — `maassubscriptions` for every rate-limiting/multi-model scenario, `maasauthpolicies` for `subscription_without_authpolicy` (ADR-018) and `multi_model_full_load` (ADR-024 — a freshly-deployed model has no gateway access at all until a matching policy exists, regardless of subscription/quota)
- `get`, `list`, `watch` on `tokenratelimitpolicies` (`kuadrant.io`, `deploy/rbac-maas-readonly.yaml`) — for `check_platform_health`/`check_model_health` (ADR-022) and `provision_subscriptions_distributed`'s post-create readiness wait (ADR-024)
- `cluster-monitoring-view` ClusterRole binding (`deploy/rbac-monitoring.yaml`, cluster-scoped — the only cluster-scoped grant the SA needs beyond its own namespace) — for querying Thanos Querier (background MaaS metrics polling)
- `create`, `delete`, `get`, `list` on `serviceaccounts` and `create` on `serviceaccounts/token`, scoped to the `maaspal` namespace (`deploy/rbac-user-provisioning.yaml`) — for `create_user`/`provision_keys_for_users` (ADR-023). **Meaningfully more sensitive than any other grant this harness holds** — minting a ServiceAccount token is a real elevated capability; review deliberately before applying, not as routine.

## Task Reference

- **`provision_api_key`**: Calls `POST /maas-api/v1/api-keys` with the SA token. Stores each created key's full record (`id`, `key`, `name`, `subscription`, `expiresAt`) in `shared_state["api_keys"]`. Supports `count` param to create N keys in a loop; sets `shared_state["task_progress"]` after each key so the UI progress chip updates. **REST-only lifecycle checks** (ADR-019): after each key, compares the response against what was requested and tallies plain-numeric counters into `shared_state["key_provision_checks"]` — `total_keys`, `name_echo_match_count`, `subscription_checked_count`/`subscription_echo_match_count` (only counted when a `subscription` param was actually passed), `expires_at_present_count` — assertable the same way any other metric is. `expect_subscription` (ADR-021, distinct from `subscription` — never sent in the request body) checks auto-selection's *outcome* without forcing it, into `expected_subscription_checked_count`/`expected_subscription_match_count`. Cleanup calls a shared `_revoke_keys()` helper (individual `DELETE /maas-api/v1/api-keys/{id}` per key).

- **`revoke_api_keys`** (ADR-019): Revokes the current key pool (`shared_state["api_keys"]`) immediately, mid-scenario — not at cleanup time — via the same `_revoke_keys()` helper `provision_api_key`'s cleanup uses. Stores `shared_state["revoked_count"]`. Lets a later task confirm inference is denied right away, not after some caching delay. The scenario's own final cleanup still runs afterward and harmlessly re-attempts DELETE on these already-gone keys.

- **`verify_api_key_search`** (ADR-019): Calls `POST /maas-api/v1/api-keys/search` with `{"name_prefix": ...}`, stores `shared_state["search_check"] = {"found_count", "expected_count"}`. REST-only; can prove the created keys are findable and that `name_prefix` filters correctly, not that a *different* caller's keys are excluded (needs a second identity, which the harness doesn't have — see `docs/architecture/empirical-verification-checklist.md`).

- **`send_requests`**: Sends concurrent OpenAI-compatible inference requests. Resolves `url` and `token` via a three-level priority chain: (1) explicit YAML `params`, (2) `shared_state`, (3) `TaskContext` defaults (MaaS model discovery + SA token). Model discovery (`_discover_model`) matches a `model:` param/`DEFAULT_MODEL` against `/v1/models`' `id`, `modelDetails.displayName`, **or `owned_by`** (`"<namespace>/<MaaSModelRef name>"`, confirmed live — the one field that reliably matches a scenario's own `target_model_namespace`/`target_model_name` config; `id`/`displayName` are cosmetic and don't need to resemble the CR name at all). Falling through to "first available" with no match is a real risk once more than one model is registered — confirmed live: an unrelated `ExternalModel` sorting first in the discovery response silently hijacked `rate_limit_validation`, which had a target model configured for its subscription but never passed it to `send_requests` at all. Every CR-targeted scenario now passes an explicit `model:` rather than relying on the fallback — `rate_limit_validation`/`rate_limit_priority_precedence` via a static `"${config.target_model_namespace}/${config.target_model_name}"`.

  **Targeting a model `deploy_simulated_model` just created this run (ADR-024)**: confirmed live that such a model is never listed in `/v1/models` — that requires full governance pairing (a `MaaSSubscription` *and* a `MaaSAuthPolicy`), not just `RuntimeReady` — so generic discovery can never resolve it and silently falls back to a *different*, wrong model. `model_from_shared_state` (single target, e.g. `subscription_without_authpolicy`) sets both `params["model"]` (the model's **bare** name) and `params["url"]` directly to that model's own dedicated per-model route (`{MAAS_API_URL}/{namespace}/{name}/v1/...` — auto-created by the LLMInferenceService controller, reachable as soon as `RuntimeReady`, and still enforced by the same gateway `AuthPolicy` as the generic route) before URL/model/token resolution runs — same self-mutating-params style as `key_index` below. For a key pool spanning *multiple* dynamically-deployed models (`multi_model_full_load`), each key's own `target_model` field (set by `provision_keys_distributed`, `"<namespace>/<name>"` format) gets its own dedicated-path client instead of one shared URL. A third, related case — a key pool with **no** explicit model and **no** per-key `target_model` (auto-selected subscription against an auto-discovered model, e.g. `single_key_load`/`multi_key_load` with both left blank) — is handled by `_resolve_models_by_subscription`: cross-references each key's own bound `subscription` against `/v1/models`' per-model `subscriptions: [{name}]` list, so an auto-selected subscription and an independently-discovered model can't end up mismatched.

  When `key_pool: true` is set, uses keys from `shared_state["api_keys"]` and distributes requests evenly across the pool (floor(M/N) per key, remainder to first). A `key_index` param (ADR-023) instead targets exactly ONE key from `shared_state["api_keys"]` by position as the task's single dedicated token — bypassing both `key_pool` (whole pool) and a static YAML `token` (can't reference a runtime-created key) — for scenarios needing to run one specific dynamically-created key at a time (e.g. one per user, see `rate_limit_per_user_or_shared.yaml`). A `result_key` param (ADR-023, default `"inference_results"`) writes results to a named `shared_state` slot instead of the one fixed key every `send_requests`-family task has always shared, so two sequential invocations can each keep independent results to compare afterward. After every completed request, updates `shared_state[result_key]` (latency, error/rate-limited/unauthorized counts, throughput, `first_rate_limited_at_tokens` once a 429 happens — ADR-024) and `shared_state["task_progress"]`, then calls `emit_assertion_state()`. Cleanup is a no-op. Also registered under `verify_revoked_key_denied` (`REGISTRY["verify_revoked_key_denied"] = SendRequestsTask`, ADR-019) and `send_requests_as_second_user` (ADR-023) — same class, second/third name, used when a scenario needs another "send some requests" step (e.g. after revoking the key pool, or targeting a second user's key) without colliding with an earlier `send_requests` step's UI chip/progress state (both are keyed by task name — see Task Progress UI below).

- **`check_maas_metrics`**: Optional explicit final metrics check. Runs the scenario's `metrics_queries:` (via `ctx.metrics_queries`, or an explicit `params.queries` override) using the shared `harness/metrics_client.py`, stores raw values in `shared_state["metrics"]`, and prints a human-readable summary to the run log. **This task class is available for explicit use but is no longer included in standard scenario task lists.** MaaS metrics are polled automatically in the background by `ScenarioRunner` (see Background Metrics Polling below).

- **`apply_rate_limit_subscription`**: Uses `kubernetes.client.CustomObjectsApi` to create or patch a `MaaSSubscription` CR (`maas.opendatahub.io/v1alpha1`) with a configured `token_limit`/`token_window`. Stores the original subscription state in `shared_state["original_subscription"]` for cleanup. Cleanup restores or deletes the CR as appropriate. Its get-or-create/patch and restore-or-delete logic is factored into module-level helpers (`_get_existing_subscription`, `_create_or_patch_subscription`, `_cleanup_subscription`, `_subscription_body`) shared with `apply_priority_test_subscriptions` below (ADR-021). After create/patch, polls `status.phase` via `_wait_for_subscription_ready()` (`ready_max_wait_s` param, default 30s) before returning — confirmed live that the K8s API accepting the write doesn't mean the MaaS controller has reconciled it yet, and `provision_api_key` right after would otherwise lose that race with `400 subscription_not_ready` (see ADR-009's third Update). `owner_groups: []` (an explicit empty list) is honored, not silently replaced by the `system:authenticated` default (ADR-023 bugfix — `self.params.get("owner_groups", _DEFAULT_OWNER_GROUPS)`, not `or`). `owner_users_from_shared_state` (ADR-023) appends usernames from a named `shared_state` list (e.g. `"users"`, populated by `create_user`) to `owner_users`, since task `params:` can't reference runtime `shared_state` via YAML templating the way assertions can. `model_from_shared_state` (ADR-018's third Update) reads `model_name`/`model_namespace` from a named `shared_state` list's first entry (e.g. `"deployed_models"`, populated by `deploy_simulated_model`) instead of requiring the literal `model_name`/`model_namespace` params, for scenarios targeting a model created fresh at runtime rather than a static admin-supplied one. `token_window` (default `_DEFAULT_TOKEN_WINDOW = "1s"`) should be set deliberately long (e.g. `"24h"`) for any scenario trying to precisely measure *when* a rate limit first triggers (ADR-024) — a short window both caps achievable sequential demand below the configured limit (so it may never trigger at all) and lets the `openai` SDK's own automatic 429 retry land in a fresh window and quietly succeed, masking a real denial from the harness even though Limitador's own counters show it happened.

- **`apply_priority_test_subscriptions`** (ADR-021): Creates *multiple* `MaaSSubscription`s in one task invocation (a `subscriptions:` list param, each with its own `name`/`priority`/`token_limit`, all sharing one `owner_groups`), tracking them as a list in `shared_state["priority_test_subscriptions"]` so each is cleaned up independently. Exists because two separate YAML entries for `apply_rate_limit_subscription` would silently clobber each other's cleanup state — that task's bookkeeping lives in fixed `shared_state` keys, not namespaced per instance (unlike the `send_requests`/`verify_revoked_key_denied` registry-alias trick in ADR-019, which works precisely because `SendRequestsTask` has no such state to collide). Each subscription individually goes through the same `_wait_for_subscription_ready()` poll as `apply_rate_limit_subscription` before moving to the next.

- **`apply_auth_policy`** (ADR-018): Uses `kubernetes.client.CustomObjectsApi` to create or patch a `MaaSAuthPolicy` CR (`maas.opendatahub.io/v1alpha1`) — the gateway-access half of the two-layer access model (a `MaaSSubscription` alone only grants quota). Same create-or-patch/restore-or-delete shape as `apply_rate_limit_subscription`, `shared_state["original_auth_policy"]`/`"_policy_created"` instead. `denied_without_auth_policy.yaml` uses this task's *absence* to test fail-closed. `model_refs_from_shared_state` covers every model `deploy_simulated_model` created this run in one policy (`multi_model_load.yaml` — confirmed live that a freshly-deployed model has no gateway access at all until a matching policy exists, regardless of subscription/quota), instead of requiring the literal `model_name`/`model_namespace` params.

- **`check_platform_health`** (ADR-022): Read-only, no cleanup needed. Reads a `TokenRateLimitPolicy` and an `HTTPRoute` for the target model — both found by **label selector** (`maas.opendatahub.io/model=<name>`, `app.kubernetes.io/name=<name>`), not an assumed generated resource name — plus a named `Gateway` (defaults to the confirmed-live `maas-default-gateway`/`openshift-ingress`, overridable). Stores `shared_state["rate_limit_policy_status"]` (`found`/`accepted`/`enforced`), `["gateway_status"]` (`programmed`), `["http_route_status"]` (`found`/`owner_ref_matches`) as 0/1 flags — missing resources default to 0 rather than raising, so an absent CR fails its assertion honestly instead of hanging PENDING.

- **`create_user`** (ADR-023): Mints `count` throwaway `ServiceAccount`s in the harness's own namespace, then a `TokenRequest`-issued token for each (`CoreV1Api.create_namespaced_service_account`/`create_namespaced_service_account_token`) — the only caller-identity-minting mechanism buildable from this harness's RBAC without IdP integration. Appends `{name, namespace, username, token}` per user to `shared_state["users"]`, where `username` is the fully-qualified `system:serviceaccount:<ns>:<name>` string confirmed live (ADR-018's Update) to be directly matchable against a `MaaSSubscription`'s `spec.owner.users[]`. Cleanup best-effort deletes each created ServiceAccount, logged, never raises.

- **`provision_keys_for_users`** (ADR-023): Requires `shared_state["users"]` (run `create_user` first). Provisions one MaaS API key per user, authenticated with **that user's own token**, not `ctx.sa_token` — so each key is minted as that caller's identity. All keys pin to one shared `subscription` param. Appends to `shared_state["api_keys"]` (the same list `send_requests`'s `key_pool` reads) with an added `owner_username`; reuses `provision_api_key`'s `key_provision_checks` counters shape unchanged. Cleanup reuses `_revoke_keys()` (deletes via `ctx.sa_token`, the harness's own admin identity, not each key's owning user — an unverified assumption, flagged in ADR-023).

- **`deploy_simulated_model`** (ADR-024): Creates `count` throwaway `LLMInferenceService` CRs using the `llm-d-inference-sim` image (no real model weights — CPU-only, random-length responses), each with a unique run-scoped name (`{name_prefix}-{run_id[:8]}-{i+1}`) so concurrent/back-to-back runs never collide. The controller auto-creates each one's `MaaSModelRef`, `HTTPRoute`, and backing `Deployment`; the task waits for `RuntimeReady` (`ready_max_wait_s`, default 120s) before moving on — **not** full `phase=Ready`, which would deadlock, since that requires a `MaaSSubscription` the *next* task hasn't created yet. `parallel` (default `true`) controls whether models deploy concurrently or one at a time. Appends `{name, namespace, isvc_created, ref_created, ready}` per model to `shared_state["deployed_models"]`, read by `provision_subscriptions_distributed`, `apply_auth_policy`'s `model_refs_from_shared_state`, and `send_requests`'/`apply_rate_limit_subscription`'s `model_from_shared_state`. **Confirmed live such a model is never listed in `GET /v1/models`** regardless of how long you wait — that needs full governance pairing (a subscription *and* an auth policy) — so anything targeting it must use the dedicated-route mechanism described under `send_requests` below, not generic discovery. Cleanup deletes each created `MaaSModelRef` then `LLMInferenceService`.

- **`provision_subscriptions_distributed`** (ADR-024): Creates `subscription_count` `MaaSSubscription`s distributed randomly across `shared_state["deployed_models"]` (1..`max_models_per_subscription` models each, weighted toward underused models for roughly even coverage; `0` = up to all models). Each goes through the same `_wait_for_subscription_ready()` poll as `apply_rate_limit_subscription`, then — **after all subscriptions are created** — a separate wait, `_wait_for_token_rate_limit_policies_ready()`, polls every unique referenced model's `TokenRateLimitPolicy` (same label-selector lookup as `check_model_health`) until both `Accepted` and `Enforced` are `True` (`token_rate_limit_ready_max_wait_s`, default 60s). This second wait exists because subscriptions sharing a model race the controller reconciling that model's single `TokenRateLimitPolicy` (confirmed live: `"the object has been modified"` conflicts) — the subscription's own `status.phase` can be set (even `"Degraded"`) before those races actually finish, so it isn't the signal that gates real traffic. Records (`{name, namespace, original, created, model_refs}`) go in `shared_state["distributed_subscriptions"]`, read by `provision_keys_distributed`; cleanup restores/deletes each independently.

- **`provision_keys_distributed`** (ADR-024): Requires `shared_state["distributed_subscriptions"]`. Creates `key_count` keys spread evenly across those subscriptions (floor(N/M) each, remainder to first). Each key record's `target_model` field is set to `"<namespace>/<name>"` of that subscription's *first* referenced model (not a bare name — `send_requests` needs both pieces to build that model's dedicated route, confirmed live a bare CR name doesn't route correctly). Appends to `shared_state["api_keys"]` (reuses `provision_api_key`'s `key_provision_checks` counters shape). Cleanup reuses `_revoke_keys()`.

- **`verify_subscription_models`** (ADR-025, `harness/tasks/subscription_check.py`): For one existing subscription (looked up via `api/maas_client.list_subscriptions()` — same image, same SA), mints one key pinned to it (reusing `ProvisionApiKeyTask`), then for each `modelRef` runs a `SendRequestsTask` burst (`retries: 0`, own `result_key`, URL from `/v1/models` `owned_by`, falling back to the per-model route rather than discovery's first-available). Small limits are probed to the first 429. Writes `shared_state["subscription_check"]` (`model_count`, `reachable_count`, `probed_count`, `enforced_count`) and a "Subscription models" table. Cleanup revokes the key. Clear error if key creation fails (likely not an owner).

- **`pause`**: Waits `duration_s` (seconds or a window string like `"1m"`) + `buffer_s`, capped at `max_s` (default 900; capped waits are called out). Reports per-second progress. A graceful stop interrupts it like any task.

- **`send_requests` additions (ADR-025)**: `retries` (OpenAI SDK `max_retries`, default 2), `stop_after_429s` (skip the rest of the burst once N requests were throttled), key-pool requests interleaved round-robin. Extra counters: `http_attempts` (every HTTP response incl. SDK retries, via an httpx response hook), `server_error_count`, `other_error_count`, `non_throttle_error_rate_pct`, `successes_after_first_429`, and — once a 429 happens — `tokens_before_first_429`, `requests_before_first_429`, `seconds_to_first_429`. Aliases `send_requests_after_window`, `send_requests_via_maas`; `verify_revoked_key_not_searchable` aliases `verify_api_key_search`.

### Background Metrics Polling

MaaS/RHOAI metrics are read from Prometheus/Thanos Querier's instant-query API (`GET {MAAS_METRICS_URL}?query=<promql>`), not a MaaS-specific REST endpoint — see ADR-014 for why, and the SA RBAC (`deploy/rbac-monitoring.yaml`, `cluster-monitoring-view`) this requires. A scenario's `metrics_queries:` block (a dict of `{name: promql}` resolved for `${config.x}` like `assertions:`/task `params:`, see `scenarios/usage_metrics_accuracy.yaml`) names which named PromQL queries to run — moved here from the global ConfigMap's `MAAS_METRICS_QUERIES` in ADR-015 so the query text lives next to the assertions that use it; `MAAS_METRICS_URL` itself stays global (cluster wiring). Confirmed and wired for the cluster this repo targets (`cluster-rkmhx.rkmhx.sandbox1230.opentlc.com`): Kuadrant/Limitador's gateway counters `authorized_calls` (total_requests) and `authorized_hits` (total_tokens — weighted per-token via the model's `TokenRateLimitPolicy`), both scoped by the `limitador_namespace` label (the target model's HTTPRoute name). See `docs/architecture/maas-metrics-reference.md` for the full catalog of every metric-emitting component found (not just these two) and ADR-014 for the decision. If deploying to a different cluster/model, re-verify these against a live `/api/v1/series` query rather than assuming — metric names/labels are confirmed to vary across Limitador deployments.

**Scoping caveat that applies to every form of MaaS-side assertion** (match form and PromQL form alike, ADR-014/ADR-015): no metric in the catalog carries a run-id or caller-id label — the finest grain confirmed live is `limitador_namespace`, shared by every caller of that model route. `authorized_calls`/`authorized_hits` aggregate *all* traffic on that route, not just this run's. Isolation is achieved purely by time-windowing (the baseline-delta subtraction below), never by a Prometheus label filter — a `promql`-form assertion's `${baseline.x}`/`${harness.x}` template variables are numeric literal substitutions, not labels, so they don't change this. Low risk on a dedicated single-model test sandbox; would need a caller-scoped label (if a deployed Limitador ever exposes one) on a busier shared cluster.

A `promql`-form assertion (ADR-015) fires its own ad hoc query each poll tick, in addition to the scenario's named `metrics_queries:` — `${baseline.<name>}` template variables resolve once, right after the baseline snapshot below, against a name from `metrics_queries:`; `${harness.<namespace>.<key>}` template variables resolve fresh every tick against live `shared_state` (e.g. `inference_results.total_requests`), since those values change continuously as tasks run. Both are handled in `harness/runner.py` (`_substitute_baseline_vars`, `_substitute_harness_vars`) — `harness/metrics_client.py:fetch_metrics` needed no changes, since it already accepts an arbitrary `{name: query}` dict and this just merges the per-assertion queries into the same per-tick batch as the named ones.

`ScenarioRunner.run()` takes one metrics snapshot immediately before the task loop starts and stores it as `shared_state["metrics_baseline"]`. It then starts an asyncio background task (`_metrics_bg`) that polls every 5 s, writes the raw current values into `shared_state["metrics"]`, computes `{name}_delta = current - baseline` for each metric present in both, and calls `emit()` to trigger an assertion re-evaluation. The delta — not the raw value — is what should be compared against harness-known sent counts, since the underlying Prometheus counter may be cumulative/scoped beyond a single run rather than zeroed per run.

**Settling metrics-dependent assertions** (`_settle_and_evaluate()` in `harness/runner.py`): a metrics-dependent assertion (any match-form assertion referencing `metrics.*`) can't be evaluated correctly right when its task finishes — Prometheus hasn't necessarily scraped the traffic yet (confirmed scrape interval on the cluster this repo targets: 30 s cluster-wide). So both the per-task assertion check (right after a task with `assertions:` completes) and the scenario's top-level assertion check (after cleanup) use the same mechanism: poll MaaS metrics every 5 s, re-evaluating the relevant assertions after each poll, stopping as soon as **all of them are PASSING** — or giving up at a `max_wait_s` cap (default 65 s) and reporting whatever the last evaluation showed. This directly checks the thing that actually matters (did the assertion pass) rather than inferring readiness from some proxy signal.

Three earlier designs tried to infer readiness indirectly instead, and each broke on a different, increasingly subtle behavior — see ADR-014 for the full account:
1. Stop on the first metrics value that differs from the run's original baseline — false positive on a longer run (the background poller usually already captured mid-run progress by the time the check ran).
2. Stop once two consecutive fetches return the same value ("settled") — false positive in the opposite direction (the first retry fetch usually lands within the same stale scrape window the background poller already saw).
3. Compare each metric's Prometheus sample timestamp to when the check started, using PromQL's `timestamp()` function — worked on a bare metric, but the actually-configured queries are wrapped in `sum(...)`, and aggregation functions reset a series' timestamp to query-evaluation time before `timestamp()` ever sees the original scrape time, so this also read as instantly "fresh."
4. **A separate, distinct bug found alongside these**: the very first "wait" implementation only applied to the scenario's *top-level* `assertions:` block, evaluated once after cleanup. But `usage_metrics_accuracy.yaml` (like other scenarios) attaches its MaaS match-assertions to the `send_requests` *task* instead — and the per-task assertion check ran a single immediate evaluation with no retry at all, failing instantly regardless of any wait logic further down. This is why `_settle_and_evaluate()` is now shared by both call sites.

`max_wait_s` is tunable per match-assertion, since different scenarios/clusters may need more or less headroom than the default:
```yaml
assertions:
  maas_requests_match:
    compare: metrics.total_requests_delta
    to: inference_results.total_requests
    tolerance_pct: 5
    max_wait_s: 65   # optional; all metrics-dependent assertions (match-form and promql-form, ADR-015) in a scenario share one settle loop, so the max configured value across them wins
```

**Task progress stays visible while settling.** `shared_state["task_progress"]` is popped (frozen into the DONE chip's final snapshot) only *after* a task's per-task assertions finish settling, not the instant `task.run()` returns — settling can take up to `max_wait_s`, and popping it early left the UI showing the task as RUNNING but with no progress data to render for that whole window, so the progress bar vanished and only reappeared once the task was finally marked DONE (confirmed live, then fixed and covered by `test_task_progress_stays_visible_during_settle_wait` in `harness/tests/test_runner.py`).

The `check_maas_metrics` task class still exists and can be added to a scenario's task list if an explicit final check with a log summary is wanted. Standard scenarios no longer include it.

### Task Progress UI

The run detail page shows a horizontal **task pipeline** above the logs. Each chip displays:
- Status icon: `○` PENDING | CSS spinner RUNNING | `✓` DONE | `✗` FAIL | `⊘` CANCELLED (a task interrupted mid-run by a graceful stop, see Stopping a Run above)
- Task name (snake_case → Title Case)
- A mini progress bar + `{current} / {total}` label when the task reports `shared_state["task_progress"]` — visible during RUNNING (including while a task's per-task assertions are settling, see Background Metrics Polling above) and preserved on completion (at 100% for DONE, actual for FAIL/CANCELLED)
- A duration readout — ticks live (client-side, off the task's `started_at`) while RUNNING, frozen to `duration_ms` once completed

Chip background colours: grey (PENDING), blue tint (RUNNING), green (DONE), red (FAIL), amber (CANCELLED).

The `TaskProgress` component polls `GET /api/runs/{id}/progress` every 2 s, managing its own interval independently of logs and assertions. Below the chips, a "What happened" list shows each task's one-line `summary`. `RunDetail.tsx` also renders, from the same progress file (`components/RunInsights.tsx`): a verdict banner, a traffic panel per burst (requests by outcome, tokens, where throttling started, successes after the first 429, latency, hidden SDK retries) with `TrafficChart.tsx` (cumulative tokens over time, non-OK requests marked by shape + reserved status colour, dashed configured-limit line, crosshair tooltip, data-table view), per-task detail tables, and a "This run created" panel.

### Scenario Categories (ADR-020, regrouped by ADR-025)

`ScenarioList.tsx` groups scenarios by a `category:` field (optional in the YAML, defaulted to `"Custom"` by `api/routes/scenarios.py` when absent) into sections organised by the user's question: Quick check, Rate limits, Access control, API keys, Usage metrics, Performance, Diagnostics, and an always-rendered Custom bucket (shown even when empty). Within a category, `kind: verify` scenarios sort before `explore`, then by `order`. Cards show `title`, `summary` and badges (uses your setup / creates temporary resources / read-only, needs extra RBAC, duration). The category order lives in two places kept in sync by hand — `CATEGORY_ORDER` in `ui/src/components/ScenarioList.tsx` and `_KNOWN_CATEGORIES` in `harness/tests/test_scenarios.py`.

### Frontend Monaco Setup

`RunSettingsModal.tsx`'s read-only YAML view (see Results Storage above) is the only place this app uses Monaco. Two deliberate choices in `ui/src/monacoSetup.ts`, both because this app otherwise bundles everything into the container image and avoids external runtime dependencies (no CDN usage anywhere else in the UI, static files served straight from the FastAPI backend per the Dockerfile):
- **Self-hosted, not CDN-loaded.** `@monaco-editor/react` defaults to lazy-fetching Monaco's AMD bundle from a public CDN at runtime — a real risk for an app meant to run inside OpenShift clusters that may have restricted egress. `monacoSetup.ts` imports `monaco-editor` directly and points `@monaco-editor/react`'s `loader.config({ monaco })` at it instead, plus configures `self.MonacoEnvironment.getWorker` to use a Vite-bundled worker (`monaco-editor/editor/editor.worker.js?worker`) rather than one Monaco would otherwise fetch itself.
- **Trimmed to YAML only.** Importing the full `monaco-editor` package entry pulls in tokenizers for every one of its ~80 bundled languages (pushed the lazy chunk over 4MB) when this app only ever displays YAML. `monacoSetup.ts` instead imports the slim core (`monaco-editor/editor/editor.api.js`) plus just the YAML language definition (`monaco-editor/languages/definitions/yaml/register.js`) — both resolved through `monaco-editor`'s package.json `exports` map, which already implies the `esm/vs/` path prefix (a doubled-prefix import path is a common mistake here and fails silently/confusingly at Rollup build time, not at dev time).
- `RunSettingsModal` itself is loaded via `React.lazy()` from `RunDetail.tsx` (not a static import) so Monaco's bundle is only fetched when a user actually opens the settings modal, not on every run page view.
- `ui/tsconfig.json` needs `"types": ["vite/client"]` for the `?worker` import's types to resolve — absent from the original tsconfig since nothing else in the app used Vite's special import suffixes.

## Scenarios

Every scenario answers one question of the form "is my MaaS behaving the way I expect, given what I set up?" (ADR-025). `kind: verify` scenarios use the cluster's existing setup and create nothing but API keys; `kind: explore` scenarios build temporary models/subscriptions/identities to probe how MaaS itself behaves. Every `send_requests`-family assertion goes through the `promql` pass-through form (ADR-015), even pure harness-side numbers — that exercises the real Thanos query path on every run, it doesn't make those checks more meaningful. Rate-limit scenarios send one request at a time with `retries: 0` and `stop_after_429s`, so a hidden SDK retry can't mask a denial and the run stops once the answer is in.

| Scenario (Category, kind) | Question | Key checks | Notes |
|---|---|---|---|
| `smoke_test` (Quick check, verify) | Is MaaS working end to end? | models listed ≥ 1; key name echo; search finds key; error rate < 5%; every request after revoke denied | REST + gateway only, no CRs. Formerly `platform_health_check`. |
| `verify_subscription` (Quick check, verify) | Does my subscription work for every model it covers? | models unreachable == 0; probed limits not enforced == 0 | `verify_subscription_models` task: one pinned key, per-model burst; models whose limit ≤ `probe_limit_max_tokens` (and window ≥ 10 s) are driven to the first 429. Results table per model. |
| `verify_subscription_rate_limit` (Rate limits, verify) | Is my subscription's rate limit enforced? | throttled at all; `tokens_before_first_429` between limit and limit + spillover; successes after first 429 == 0; Limitador `limited_calls` delta > 0 | `mode: existing` (default — limit/window autofilled from the subscription) or `temporary` (creates `maaspal-rate-limit-test`, 24h window). Merged from `rate_limit_validation` + `rate_limit_validation_existing_subscription`. |
| `keys_share_user_budget` (Rate limits, verify) | Do all my keys share one budget? | combined tokens before first 429 within [limit, limit + spillover]; no key succeeds after the first 429 | N keys, round-robin, sequential. TRLP counters are per `auth.identity.userid`, so "shared" is the documented behaviour. |
| `rate_limit_window_recovery` (Rate limits, verify) | Does access come back after the window? | throttled before the wait; successes after the wait > 0 | `pause` waits the subscription's window + buffer, capped at `max_wait_s` (900 s). |
| `subscription_auto_selection` (Rate limits, explore) | Which subscription do my keys get? | auto-selected key bound to the higher-priority subscription; tokens sent > low limit; 429s == 0 | Two temporary subscriptions (priority 50/10 tokens vs 200/1M). Formerly `rate_limit_priority_precedence` (ADR-021). |
| `rate_limit_per_user_or_shared` (Rate limits, explore) | Are limits per user or shared? | user A throttled at the budget; user B per `expected_behavior` (`per_user`: own full budget; `shared`: throttled below the limit) | Two minted ServiceAccounts on one temporary subscription. Needs `deploy/rbac-user-provisioning.yaml`. Formerly `rate_limit_shared_across_users` (ADR-023). |
| `denied_without_auth_policy` (Access control, explore) | No auth policy → access denied? | > 90% rejected; 401/403 > 0; 429 == 0 | Throwaway model + identity + quota-only subscription. Formerly `subscription_without_authpolicy` (ADR-018). |
| `api_key_lifecycle` (API keys, verify) | Do API keys behave correctly? | name echo / expiry present / search finds all; error rate < 5% before revoke; all denied after; revoked keys no longer listed as active | REST-only (ADR-019). |
| `usage_metrics_accuracy` (Usage metrics, verify) | Do MaaS usage metrics match real traffic? | `authorized_calls`/`authorized_hits` baseline-deltas match requests/tokens sent within tolerance | `limitador_namespace` autofilled from the model's HTTPRoute. Formerly `metrics_fill` (ADR-014/015). |
| `load_test` (Performance, verify) | How does MaaS hold up under load? | error rate excluding 429s < max; p99 < max | `key_count` (default 1) spreads requests over keys. Merged from `single_key_load` + `multi_key_load`. |
| `gateway_overhead` (Performance, verify) | How much latency does the gateway add? | error rates; p50 via MaaS − p50 direct < `max_overhead_ms` | Direct leg needs `target_url`/`target_model` (+ `target_token`); `compare_with_maas: "no"` = plain direct load test. Formerly `direct_inference`. |
| `multi_model_load` (Performance, explore) | Does MaaS hold up with many models and subscriptions? | error rate excluding 429s < 5% | `request_count: 0` (+ auto cleanup off) just builds the environment. Merged from `multi_model_full_load` + `multi_model_subscription_spread` (ADR-024). |
| `model_config_health` (Diagnostics, verify) | Are my models wired up correctly? | gateway Programmed; models missing Ready / subscription / auth policy / namespace gateway label / enforced TRLP / route ownership == 0 | Read-only; blank model = every internal model. Reuses `api/maas_client.list_models()`. Formerly `model_health_check` (ADR-022). |

**Design note**: Tasks are kept atomic and composable so future scenarios can reuse just `provision_api_key`, just `send_requests`, etc.

## Future Features / Scenario Ideas

- **Historic metadata testing**: Pre-populate metrics store with backdated requests to simulate a year of data. Needs research into Prometheus remote-write or MaaS-specific APIs. **Requires cluster admin** — writing backdated data directly to the metrics store is not possible with rhoai-admin alone.
- **Per-user auth**: Inherit the permissions of the UI user (OIDC token passthrough) instead of always using the SA token.
- **Group limit probing**: How many groups can a user have in a subscription? Scenario that probes this limit.
- **External model enumeration**: How many external models can a subscription have? Scenario that validates external model routes (OpenAI/Bedrock/Gemini) through the MaaS gateway.
- **ExternalModel / ExternalProvider YAML inspection in the dashboard**: Add a read-only YAML view (mirroring `RunSettingsModal`'s Monaco viewer) for `ExternalModel` and the controller-derived `ExternalProvider` CRs, so their spec/status can be inspected/debugged from the MaaS:PAL UI instead of `oc get -o yaml`. Motivated by live debugging on 2026-09-21: setting up an `ExternalModel` pointed at an already-MaaS-hosted model (as an OpenAI-compatible stand-in, since no real third-party provider was available to test against) surfaced non-obvious controller-derived state — an auto-created `ExternalProvider`, `HTTPRoute`, and `ExternalName` `Service` — that's currently only visible via direct cluster access, and a 401 on the resulting inference path that's still unexplained (see empirical-verification-checklist.md candidate entry).

## Implementation Plan

See [`docs/project/implementation-plan.md`](docs/project/implementation-plan.md) for the full phased plan including milestones for CI, documentation, and deployment. Summary of phases:

| Phase | Scope |
|---|---|
| 0 | Project foundation: repo scaffold, pyproject.toml, package.json, Makefile, CI skeleton |
| 1 | Harness core: Task ABC, `emit_assertion_state()`, config loader, assertion evaluator, ScenarioRunner |
| 2 | Task implementations: `auth.py`, `inference.py`, `metrics.py` (stub), `subscription.py` |
| 3 | Scenario YAMLs: all five scenarios with schema validation |
| 4 | API server: FastAPI routes, SQLite, K8s job management, REST log polling |
| 5 | Frontend: React + PatternFly 5 UI — MaaS:PAL theme, scenario config editor, run detail page, live assertion panel, run history |
| 6 | Containerization: multi-stage Dockerfile, all deploy manifests, finalized Makefile |
| 7 | Integration & E2E testing on a live RHOAI cluster |
| 8 | CI/CD: lint + unit test + build on PRs; image push on release tags |
| 9 | Documentation: quickstart, scenario authoring guide, task dev guide, runbook, API reference |

## Verification

**Note on CR-based scenarios specifically**: every scenario using `kubernetes.client.CustomObjectsApi()` (`verify_subscription_rate_limit`, `denied_without_auth_policy`, `subscription_auto_selection`, `model_config_health`) was, until the ADR-009 update above, broken on every real live run — the harness Job process never configured the Kubernetes client at all, so every such task failed immediately with `urllib3.exceptions.LocationValueError: No host specified`, regardless of anything else being correct. This was only caught once `model_config_health` was actually triggered live for the first time. Fixed in `harness/main.py:_load_kube_config()` and **confirmed live**: `model_config_health`, `subscription_auto_selection`, `verify_subscription_rate_limit`, and `denied_without_auth_policy` all now pass end-to-end on a real cluster (the latter two needed further fixes beyond this one — see ADR-024 and Verification item 8 below).

1. **Unit tests**: `make test` — runs `pytest harness/tests/` (mocked HTTP) + Jest (UI components)
2. **Local harness**: `python -m harness.main --scenario load_test --run-id test-123` (needs real MaaS cluster env vars)
3. **Local dev**: `make dev` — starts FastAPI dev server + Vite dev server; open browser, verify scenario list loads and assertion panel renders
4. **Build**: `make build` — multi-stage Docker build (Node UI build → Python image)
5. **End-to-end**: `make deploy` (`oc apply -k .`) → open Route URL → pick scenario → edit config overrides in modal → start run → confirm task pipeline chips appear within ~2 s and update (RUNNING with progress bar → DONE green) → confirm logs stream and scroll smartly (scroll up to see "N new lines" badge) → confirm assertion panel updates independently → navigate away and back (browser back button should work via URL hash) → verify results in history → verify no leftover `maaspal-*` MaaS API keys → verify `MaaSSubscription` CR state restored after `verify_subscription_rate_limit`
6. **MaaS metrics cross-check specifically**: run `usage_metrics_accuracy` (needs `MAAS_METRICS_URL` set and `deploy/rbac-monitoring.yaml` applied — the scenario's own `metrics_queries:` block supplies the PromQL) → confirm `maas_requests_match`/`maas_tokens_match` go PASSING, not just `error_rate_pct` — these only appear on `usage_metrics_accuracy`'s run page, not on other scenarios' (they aren't in those scenarios' `assertions:` blocks) — while settling, `send_requests`'s progress bar should stay visible the whole time rather than disappearing and popping back at the end. The `clamp_min(vector(...), 1)` fix has been confirmed valid against a live Thanos Querier directly (`cluster-rkmhx.rkmhx.sandbox1230.opentlc.com`) but not yet re-verified via an actual end-to-end scenario run.
7. **Runtime display / view settings / stop, specifically**: start a `load_test` run with a large `request_count` → confirm total and per-task elapsed time visibly tick up once a second in the UI while `RUNNING`, and freeze to a sensible final value once the run completes (matching `RunHistory`'s Duration column) → click "View Settings" mid-run and confirm the YAML shown matches what was actually configured, including any launch-modal edits, with no raw secrets/tokens visible (should show `***REDACTED***`) → click "Stop" mid-run on a scenario that provisions API keys (e.g. `load_test` with `key_count: 5`) → confirm the run reaches `CANCELLED` (not stuck `RUNNING`, not `FAIL`) within `_STOP_GRACE_PERIOD_S`, and verify via the MaaS API / `oc` that no `maaspal-*` keys were left behind (needs `deploy/rbac.yaml`'s `delete` verb on `pods` applied).
8. **Enforcement cross-checks specifically (ADR-018, ADR-024)**: run `verify_subscription_rate_limit` → confirm `first_rate_limit_reaches_budget`/`first_rate_limit_within_spillover`/`maas_denied_by_rate_limit` all go PASSING and `oc get limitador`/the MaaS Setup Rate Limiting tab shows `limited_calls` actually incremented, not just the pre-existing harness-side assertions. Run `denied_without_auth_policy` → confirm `deploy_simulated_model` creates and readies its own throwaway `LLMInferenceService`/`MaaSModelRef` in `llm`, then confirm the run reaches PASS (meaning requests were genuinely denied for auth reasons, `unauthorized_count > 0` and `rate_limited_count == 0`) → confirm cleanup leaves no orphaned `MaaSSubscription`, no orphaned `maaspal-fail-closed-user-*` ServiceAccount (needs `deploy/rbac-user-provisioning.yaml` applied), and no orphaned `maaspal-fail-closed-model-*` `LLMInferenceService`/`MaaSModelRef` (`oc get llminferenceservice,maasmodelref -n llm`). **Both confirmed passing live.** Getting here took three rounds of fixes beyond ADR-018's original third Update: (1) the dedicated-per-model-route mechanism (ADR-024 §1), since a freshly-deployed model is never listed in `/v1/models` regardless of readiness; (2) replacing `verify_subscription_rate_limit`'s throughput/error-rate assertions with the precise `first_rate_limited_at_tokens`-based ones (ADR-024 §4) plus a long `token_window` (ADR-024 §5), since a tight window and aggregate-rate assertions were both actively self-contradicting against this scenario's own goal of *triggering* the limit; (3) the `token_limit`/`token_window` redaction exception (ADR-024 §6) so these values are actually visible in Run Settings while debugging. See `docs/architecture/empirical-verification-checklist.md` for the full backlog of what else is worth checking this way.
9. **Multi-model load/routing (ADR-024)**: run `multi_model_full_load` → confirm it reaches PASS (`error_rate_pct < 5`) with every request actually landing on its intended model, not a mismatched/first-available one → confirm cleanup leaves no orphaned `maaspal-sim-*` models, `maaspal-dist-sub-*` subscriptions, the `maaspal-dist-policy` `MaaSAuthPolicy`, or `maaspal-dist-key-*` keys. **Confirmed passing live.** If it regresses, suspect the per-model dedicated-route assumption first (ADR-024's Negative consequences) — it's confirmed on one cluster only, not derived from the LLMInferenceService controller's source.
10. **REST-only API key lifecycle (ADR-019), not yet run against a live cluster**: run `api_key_lifecycle` → confirm all 5 assertion groups go PASSING → in the UI's task pipeline, confirm `verify_revoked_key_denied` renders as its own distinct chip after `revoke_api_keys`, not overlapping/replacing the earlier `send_requests` chip (validates the registry-alias fix for the task-name collision documented in ADR-019) → confirm via `POST /maas-api/v1/api-keys/search` by hand that no `maaspal-lifecycle-key*` keys remain afterward.
11. **Scenario categories (ADR-020)**: open the scenario list in the UI → confirm 6 populated category sections (Load Testing, Rate Limiting, Access Control, Metrics Validation, API Key Lifecycle, Platform Health) in that order, plus a visible, empty "Custom" section with its placeholder text → drop a scenario YAML with no `category:` field into `scenarios/` and confirm it appears under Custom without any code change.
12. **Rate-limit priority precedence (ADR-021), not yet run against a live cluster**: run `subscription_auto_selection` → confirm `expected_subscription_match_count == 1` (the auto-selected key actually bound to the higher-priority subscription) → confirm via `oc get maassubscriptions -n models-as-a-service` that both `maaspal-priority-*` subscriptions are gone after the run. If the assertion instead shows a mismatch, don't assume the harness code is wrong first — re-verify live whether `system:authenticated` eligibility and pure-integer-priority-wins are both still accurate for this cluster's MaaS version (see ADR-021's Negative consequences).
13. **Platform health checks (ADR-022), not yet run against a live cluster**: run `model_config_health` → confirm all 4 assertions PASS → cross-check by hand (`oc get tokenratelimitpolicy/gateway/httproute`) that the flags match reality → confirm it fails honestly (not PENDING) if pointed at a model/gateway/namespace combination where one of the three resources doesn't exist.
14. **Multi-user rate-limit sharing (ADR-023, ADR-024)**: apply `deploy/rbac-user-provisioning.yaml` first (review its sensitivity before applying) → run `rate_limit_per_user_or_shared` → confirm two `ServiceAccount`s are minted and both keys land on the one shared `MaaSSubscription` (`subscription_echo_match_count == 2`) → confirm `first_rate_limit_reaches_budget_user_a` PASSES (user A's solo burst genuinely exhausts the full 50-token shared budget — the necessary precondition for user B's result to mean anything) → read whichever way `first_rate_limit_reaches_budget_user_b` lands: PASS suggests per-user independent allowances (user B's own untouched budget exhausts at roughly the same token count user A's did), FAIL suggests a shared pool (user B gets denied far below `token_limit`, since user A's burst already spent it) — **either is a valid, useful result**, treat a FAIL as the finding, not a bug report, per ADR-023 → confirm cleanup leaves no orphaned ServiceAccounts (`oc get sa -n maaspal | grep maaspal-pool-user`) and no orphaned `MaaSSubscription` (`oc get maassubscriptions -n models-as-a-service`). Uses the same `token_limit: 50`/`token_window: "24h"`/`concurrency: 1` defaults as `verify_subscription_rate_limit` (ADR-024), for the same reasons. **Confirmed passing live** (user A's precondition assertion; user B's result is the empirical answer either way, not a pass/fail bar on its own).

14. **Question-first UX (ADR-025)**: open the scenario list → confirm titles read as questions with badges, grouped Quick check → Diagnostics → launch `verify_subscription_rate_limit`, pick a subscription with a small limit (e.g. `simulator-free`, 100/1m) and its model → confirm Token limit/Window autofill and Advanced → `limitador_namespace` is filled → confirm the "What this run will do" sentence → run it → confirm the verdict banner, "What happened" narration, the traffic chart's cut-off near the dashed limit line, cards reading real values (e.g. "115 tokens, expected 100 – 200"), `http_attempts == total_requests` (no hidden retries), and "This run created … All cleaned up ✓". Then run `verify_subscription` and `rate_limit_window_recovery` against the same subscription. Old runs in history should show their scenario's new title.
