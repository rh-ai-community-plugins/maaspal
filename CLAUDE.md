# MaaS:PAL - RHOAI MaaS Testing Harness

## Context

A testing harness for Red Hat OpenShift AI (RHOAI) Models as a Service (MaaS). Validates existing RHOAI environments by running test scenarios against live endpoints. The harness user will typically be an admin, but uses RHOAI's dedicated endpoints and functionality as intended (not bypassing RHOAI's own access controls — acting through RHOAI's user-facing APIs, not raw cluster admin operations).

Tests are composed of atomic **tasks** (e.g., provision API key, send inference requests) grouped into **scenarios** (user-selectable flows defined in YAML). The harness runs in-cluster as Kubernetes Jobs, orchestrated by a UI that is an **RHOAI Dashboard community plugin** (ADR-026): a Webpack Module Federation remote under *Community plugins → MaaS:PAL*, following the `rh-ai-community-plugins/hello-world` seed project.

## Architecture Overview

```
                  RHOAI Dashboard (host) — loads /_mf/maaspal/remoteEntry.js
                  Plugin UI (React + TypeScript + PatternFly 6, Module Federation remote,
                  nginx Deployment "maaspal" :8080)
                  - Scenarios catalog (search; category, custom/built-in, type filters);
                    pick a scenario, override config params, start run
                  - Live log polling (REST, 1s interval) with smart scroll
                  - "Send from user browser" steps: claims the step, sends straight to
                    the MaaS gateway, reports raw per-request records (ADR-027)
                  - Live assertion status panel (2s poll, independent of logs)
                  - Task progress pipeline (2s poll)
                  - Results history + per-run detail view; routes /maaspal/scenarios, /maaspal/runs[/<id>], /maaspal/overview (/maaspal/setup redirects)
                  - Run page narration: verdict/finding, steps with per-resource
                    cleanup status, traffic and metrics charts, collapsible logs
                          |  /maaspal/api/*  → dashboard proxyService (authorize: true,
                          |  user's token forwarded) → /api/*
                  FastAPI BFF (Deployment "maaspal-bff" :3000)
                  - Every route: SelfSubjectAccessReview with the user's token (api/auth.py)
                  - GET  /api/health                          (ungated, probes)
                  - GET  /api/scenarios
                  - POST /api/runs  (accepts config_overrides)
                  - GET  /api/runs, /api/runs/{id}
                  - POST /api/runs/{id}/stop                  (graceful stop, see below)
                  - POST /api/runs/{id}/auto-cleanup, /cleanup (toggle; manual "Clean Up Now")
                  - GET  /api/maas/*                         (read-only MaaS overview page, ADR-017)
                  - GET  /api/runs/{id}/logs/lines?offset=N  (REST poll)
                  - GET  /api/runs/{id}/assertions            (reads PVC file)
                  - GET  /api/runs/{id}/progress              (reads PVC file)
                  - GET  /api/runs/{id}/config                (reads PVC file, YAML text)
                  - GET  /api/runs/{id}/browser-work, POST …/browser-work/claim,
                    POST /api/runs/{id}/browser-results       ("Send from user browser", ADR-027)
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
├── src/                        # Frontend: Module Federation remote (React 18 + TypeScript + PatternFly 6), built by webpack to dist/
│   ├── index.ts → bootstrap.tsx # Standalone dev entry only: mounts App at /maaspal/* like the dashboard does
│   ├── rhoai/
│   │   ├── extensions.ts       # Exposed as ./extensions: Community plugins section (shared), MaaS:PAL section, "Scenarios"/"Runs"/"MaaS overview" links, /maaspal/* route
│   │   └── CommunityNavIcon.tsx # [SHARED] community plugins icon — never edit
│   └── app/
│       ├── App.tsx             # react-router routes under /maaspal: scenarios, runs, runs/:runId, overview (setup → overview redirect); "MaaS:PAL" header; CommunityBanner; no <Page> (the dashboard owns the chrome)
│       ├── logos.ts            # Header mascot: light/dark logo sets (assets/logos/), follows the dashboard's pf-v6-theme-dark class, new pick on every page
│       ├── api/client.ts       # Typed fetch wrappers + types for every route; API_BASE = /maaspal/api (the dashboard's proxyService)
│       ├── launchForm.ts       # Launch-form helpers: autofill, show_if, required gating, plan sentence
│       ├── scenarioTitles.ts   # Scenario id → title (incl. previous_names), name formatting
│       ├── monacoSetup.ts      # Self-hosted, YAML-only Monaco (see Frontend Monaco Setup)
│       ├── browserSender.ts    # "Send from user browser": fetch-based burst (request bodies, SSE, SDK retry rules, blocked detection)
│       ├── useBrowserSender.ts # Run page hook: claims a waiting browser step and runs it from this tab (ADR-027)
│       ├── status.ts           # Every status (run/task/check/cleanup) → tone + icon + PF Label colour
│       ├── styles/tokens.css   # The colour tokens, light + dark (pf-v6-theme-dark) — the only file with colour literals
│       ├── styles/colors.ts    # Typed token refs for inline styles/SVG (COLOR, toneColor, toneBg)
│       ├── styles/theme.css    # Component styles, scoped to .maaspal-plugin / maaspal-* classes; tokens only
│       └── components/
│           ├── CommunityBanner.tsx/.css # [SHARED] required "Community Plugin" banner — never edit
│           ├── MaaspalNavIcon.tsx    # Sidebar icon, exposed as ./Icon
│           ├── PageIntro.tsx         # Page title + one-line explanation (Scenarios, Runs, MaaS overview)
│           ├── ScenarioCatalog.tsx   # Scenarios page: filter rail (search, categories, custom/built-in, type) + card gallery, filters in the URL
│           ├── RunTrigger.tsx        # Launch modal: labelled inputs, ⓘ help, Advanced section, autofill, "What this run will do"
│           ├── RunHistory.tsx        # Past runs (titles via scenarioTitles)
│           ├── RunDetail.tsx         # Run page: metadata bar, verdict/finding, chips, steps, traffic, tables, checks, logs panel
│           ├── TaskProgress.tsx      # Live task chips (progress bars incl. token/step progress, durations)
│           ├── RunInsights.tsx       # Verdict, findings, "What happened" steps (with per-resource cleanup), traffic panels, step-load results, tables
│           ├── TrafficChart.tsx      # Tokens over time, one or several bursts on one timeline, limit line, waits shaded
│           ├── MetricsComparisonChart.tsx # MaaS-reported vs sent over time
│           ├── AssertionPanel.tsx    # Check cards (label, value, target, details)
│           ├── LogStream.tsx         # 1 s log polling, smart scroll
│           ├── RunSettingsModal.tsx, RawYamlModal.tsx # Read-only Monaco YAML views (run settings; CRs in the MaaS overview page)
│           └── maas/                 # MaaS overview page (ADR-017): Models, Subscriptions, Authorization Policies, Access Control, Access Simulator, Rate Limiting, Networking, Platform Config
│
├── bff/                        # Python project: FastAPI BFF + the harness (same image, bff/Containerfile)
│   ├── pyproject.toml
│   ├── Containerfile           # UBI9 Python 3.11, UID 1001, uvicorn on :3000; also every harness Job's image
│   ├── harness/                    # Test runner (K8s Job entrypoint)
│   │   ├── main.py                 # Job entrypoint: loads kube client config (ADR-009 update), SIGTERM handler, runs the scenario, writes /data/results/<id>.json
│   │   ├── runner.py               # ScenarioRunner: task loop, assertions, metrics polling, graceful stop, cleanup; writes progress/assertions/config files (incl. the run page's narration, traffic, resources, tables, findings, verdict)
│   │   ├── result.py               # RunResult/TaskResult dataclasses + assertion evaluation (simple, match, promql; between/label/unit/target; live ${harness.x} bounds)
│   │   ├── config.py               # Config loader: global ConfigMap + scenario YAML + launch overrides; task `when:` filtering
│   │   ├── cleanup_state.py        # Auto-cleanup flag, persisted shared_state, cleanup status, per-resource cleanup marking (shared with api/cleanup.py)
│   │   ├── browser_channel.py      # "Send from user browser" PVC files: order / claim / records (shared with api/routes/browser.py, ADR-027)
│   │   ├── metrics_client.py       # fetch_metrics: Thanos Querier client
│   │   ├── durations.py            # parse_duration_s: "30s"/"1m"/"24h" → seconds
│   │   └── tasks/
│   │       ├── base.py             # Task ABC, TaskContext, record_created()
│   │       ├── registry.py         # task name → class (incl. aliases)
│   │       ├── auth.py             # provision_api_key, revoke_api_keys, verify_api_key_search, provision_keys_distributed (REST-only)
│   │       ├── identity.py         # create_user, provision_keys_for_users (ADR-023)
│   │       ├── inference.py        # send_requests: fixed bursts, until_throttled ramp, step load (stages)
│   │       ├── subscription.py     # apply_rate_limit_subscription, apply_priority_test_subscriptions, provision_subscriptions_distributed
│   │       ├── subscription_check.py # read_subscription_limits, discover_subscription_models, send_requests_to_each_model (ADR-025)
│   │       ├── request_types.py    # send_requests_each_type: every request type via MaaS + direct, "Failed at" MaaS or model
│   │       ├── access_policy.py    # apply_auth_policy (ADR-018)
│   │       ├── model.py            # deploy_simulated_model (ADR-024), expose_model_route + probe_direct_endpoint (gateway_overhead)
│   │       ├── platform_health.py  # check_platform_health (REST smoke), check_model_health (read-only model wiring, one or all models)
│   │       ├── analysis.py         # classify_rate_limit_pooling: two users' bursts → per-user/shared finding
│   │       ├── metrics.py          # check_maas_metrics (optional explicit metrics task; not used by built-in scenarios)
│   │       └── stubs.py            # pause (production: waits out rate-limit windows), stub_pass/stub_fail (tests)
│   │
│   ├── scenarios/                  # Scenario YAMLs (baked into the BFF image) — see "Scenarios" below
│   │   ├── smoke_test.yaml, verify_subscription.yaml, request_types.yaml     # Quick check
│   │   ├── verify_subscription_rate_limit.yaml, keys_share_user_budget.yaml,
│   │   │   rate_limit_window_recovery.yaml, subscription_auto_selection.yaml,
│   │   │   rate_limit_per_user_or_shared.yaml                                # Rate limits
│   │   ├── denied_without_auth_policy.yaml                                   # Access control
│   │   ├── api_key_lifecycle.yaml                                            # API keys
│   │   ├── usage_metrics_accuracy.yaml                                       # Usage metrics
│   │   ├── load_test.yaml, gateway_overhead.yaml, multi_model_load.yaml      # Performance
│   │   ├── model_config_health.yaml                                          # Diagnostics
│   │   └── stub.yaml, stub_failing.yaml                                      # test fixtures, hidden from the UI
│   │
│   ├── api/                        # FastAPI backend
│   │   ├── main.py                 # App (every route gated by auth.py), /api/health, background poller that finalizes runs (incl. runs whose harness died without a result)
│   │   ├── auth.py                 # Access gate: SelfSubjectAccessReview with the dashboard user's token (ADR-026)
│   │   ├── db.py                   # SQLite (aiosqlite)
│   │   ├── k8s.py                  # create_job (backoffLimit 0), stop_run (suspends the Job), run_job_state, delete_stopped_job, log capture to PVC
│   │   ├── cleanup.py              # Manual "Clean Up Now": re-runs task cleanup() from persisted state
│   │   ├── maas_client.py          # Read-only MaaS domain model for the MaaS overview page (also reused by harness tasks — same image)
│   │   └── routes/
│   │       ├── scenarios.py        # GET /api/scenarios (config defaults + display/launch metadata)
│   │       ├── runs.py             # POST /api/runs, GET /api/runs[/{id}], POST /api/runs/{id}/stop|auto-cleanup|cleanup
│   │       ├── logs.py             # GET /api/runs/{id}/logs/lines?offset=N
│   │       ├── assertions.py       # GET /api/runs/{id}/assertions
│   │       ├── progress.py         # GET /api/runs/{id}/progress
│   │       ├── config.py           # GET /api/runs/{id}/config (YAML text)
│   │       ├── browser.py          # GET /api/runs/{id}/browser-work, POST …/browser-work/claim, POST …/browser-results (ADR-027)
│   │       └── maas.py             # GET /api/maas/{status,subscriptions,models,access,auth-policies,rate-limit-policies,limitador,gateways,http-routes,platform}
│
├── chart/                      # Helm chart maaspal-chart (replaces the old deploy/ + kustomize)
│   ├── values.yaml             # namespace cp-maaspal, maas.* settings, images, access.users/groups, rbac.* toggles, networkPolicy
│   └── templates/              # frontend + BFF Deployments/Services, PVC, global ConfigMap, SA, Roles/ClusterRoles (one file per old rbac-*.yaml), maaspal-user Role, NetworkPolicy, NOTES
│
├── config/webpack.{common,dev,prod}.js # ModuleFederationPlugin (name maaspal, exposes ./extensions + ./Icon, shared singletons); dev proxies /maaspal/api → :3000
├── docs/
│   ├── design/colors-and-components.md    # Colour tokens (light/dark), status mapping, component guide
│   ├── architecture/
│   │   ├── adrs/                          # Architecture Decision Records (ADR-001 to ADR-027)
│   │   ├── maas-domain-reference.md       # MaaS governance objects (subscriptions, models, policies, Kuadrant, gateway) as found live
│   │   ├── maas-metrics-reference.md      # Every MaaS/RHOAI metric found live, not just the ones MaaS:PAL uses
│   │   └── empirical-verification-checklist.md  # Which claims are verified against a live cluster (Verified/Partial/Gap)
│   ├── deployment/OPENSHIFT_DEPLOY.md     # Install, register with the dashboard, access, permissions, chart reference
│   └── project/
│       └── implementation-plan.md  # Original phased plan (historical)
│
├── plugin.yaml                 # Community plugin manifest (identity, compatibility, images, install, remote, RBAC)
├── Containerfile               # Frontend image: webpack build → UBI9 nginx serving dist/ on :8080 (CORS on remoteEntry.js)
├── package.json                # Frontend deps/scripts + module-federation block
├── Makefile                    # install, lint, typecheck, test, validate, build, dev(-standalone|-bff), image-*, chart-*, deploy
├── scripts/                    # build-push.sh, scan-image.sh, sync-chart-version.js (npm `version` hook: chart, pyproject, plugin.yaml)
└── README.md                   # Overview, quick start (install, register, access), permissions, develop
```

## Key Design Decisions

### Images
The plugin ships two images (ADR-026). The frontend (`Containerfile`, `quay.io/rh-ai-community-plugins/maaspal`) is nginx serving the webpack build. The BFF and the harness Job share one image (`bff/Containerfile`, `…/maaspal-bff`), with different entrypoints:
- BFF: `uvicorn api.main:app --port 3000`
- Job runner: `python -m harness.main --scenario <name> --run-id <uuid>` (image from `MAASPAL_IMAGE`; ServiceAccount, global ConfigMap and PVC names from `MAASPAL_SERVICE_ACCOUNT`/`MAASPAL_GLOBAL_CONFIGMAP`/`MAASPAL_DATA_PVC`, all set by the chart's global ConfigMap)

### Access gate (ADR-026)
The BFF acts with the `maaspal` ServiceAccount, so every route except `/api/health` runs `api/auth.py:require_user` (a dependency on the whole FastAPI app): it needs the dashboard-forwarded `Authorization: Bearer <user token>` (401 otherwise) and makes a **SelfSubjectAccessReview with that token** for verb `use` on `harness.maaspal.rh-ai-community-plugins.io` in the plugin namespace (403 if denied, 503 if the API server is unreachable). That resource is virtual — granted only by the chart's `maaspal-user` Role (`access.users`/`access.groups`), giving no real cluster power; cluster-admins pass. Decisions are cached 60 s per token hash. `MAASPAL_AUTH_MODE=off` (local dev, and `api/tests/conftest.py`) disables it. A NetworkPolicy limits BFF ingress to the dashboard namespace.

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

1. **Global ConfigMap** (`chart/templates/configmap-global.yaml`, from the chart's `maas.*` values; the MaaS and Thanos URLs default to ones derived from the cluster's apps domain): cluster-level defaults, injected as env vars into the BFF and every Job. Also carries `NAMESPACE` (the plugin namespace), which scenarios can reference as `${config.NAMESPACE}`
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
A trimmed version of `scenarios/rate_limit_window_recovery.yaml`:
```yaml
name: rate_limit_window_recovery
title: "Does access come back after the rate-limit window?"   # what the UI shows
summary: "Uses up a small token budget, waits for its window to pass, and checks requests succeed again."
description: >-                     # shown under "More details" in the launch form
  Creates a temporary subscription with a small limit over a short window...
category: "Rate limits"             # optional — omitted or unknown lands in "Custom" (a scenario not in BUILTIN_SCENARIOS is also labelled Custom)
kind: explore                       # verify = uses your setup (API keys only); explore = creates temporary resources
mutates: [api_keys, subscriptions]
requires: [target_model_name, target_model_namespace]   # gates Launch
plan_template: >-
  Create a temporary subscription allowing ${config.token_limit} tokens per ${config.token_window}
  on ${model}, send until throttled, wait, then send ${config.recovery_requests} more.
inputs:                             # per-config-key launch-form metadata
  target_model_name: {label: "Model"}
  token_window: {label: "Window", help: "How long a token budget lasts before it refills."}
  token_limit: {advanced: true, help: "Small, so it's used up in seconds."}
config:                             # flat defaults; the launch form overrides them
  target_model_name: ""
  target_model_namespace: ""
  token_window: "1m"
  token_limit: 50
  recovery_requests: 3
verdict:
  pass: "Throttled after ${harness.inference_results.tokens_before_first_429} tokens, and access came back."
  fail: "Access did not come back after the ${config.token_window} window."
tasks:
  - name: apply_rate_limit_subscription
    params:
      new_subscription_name: "maaspal-window-recovery-test"
      namespace: "models-as-a-service"
      model_name: "${config.target_model_name}"
      model_namespace: "${config.target_model_namespace}"
      token_limit: "${config.token_limit}"
      token_window: "${config.token_window}"
  - name: provision_api_key
    params: {key_name: "maaspal-recovery-key", subscription: "maaspal-window-recovery-test"}
  - name: send_requests
    params:
      model: "${config.target_model_namespace}/${config.target_model_name}"
      key_pool: true
      retries: 0
      until_throttled: true
      limit: "${config.token_limit}"
      window: "${config.token_window}"
      chart: true
      chart_group: "recovery"
      label: "Before the wait"
    assertions:                     # per-task checks, evaluated when the task finishes
      throttled_at_all:
        label: "Budget used up (throttled)"
        unit: "429s"
        promql: "${harness.inference_results.rate_limited_count}"
        expect: "> 0"
  - name: pause
    params: {duration_s: "${config.token_window}", buffer_s: 5, reason: "waiting for the window to reset"}
  - name: send_requests_after_window
    params:
      model: "${config.target_model_namespace}/${config.target_model_name}"
      count: "${config.recovery_requests}"
      key_pool: true
      retries: 0
      result_key: "inference_results_after_window"
      chart: true
      chart_group: "recovery"
      label: "After the wait"
    assertions:
      recovered_success_count:
        label: "Requests that succeeded after the wait"
        promql: "${harness.inference_results_after_window.success_count}"
        expect: "> 0"
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
- Available metrics from `send_requests` (via `shared_state["inference_results"]`): `error_rate_pct`, `p50/p95/p99_latency_ms`, `throughput_rps`, `total_requests`, `success_count`, `fail_count`, `rate_limited_count` (429s), `unauthorized_count` (401/403s — both caught via `openai.APIStatusError.status_code`, ADR-018, so a scenario can assert on *why* requests failed, not just whether), `total_tokens_sent`, `prompt_tokens_sent`, `completion_tokens_sent`, `first_rate_limited_at_tokens` (ADR-024 — cumulative `total_tokens_sent` at the moment of the *first* 429; **absent**, not `0`, until a 429 actually happens, so a referencing assertion stays honestly PENDING instead of reading a false zero — see `verify_subscription_rate_limit.yaml`/`rate_limit_per_user_or_shared.yaml`) Since ADR-025 also: `http_attempts` (incl. SDK retries), `server_error_count`, `not_found_count`, `other_error_count`, `non_throttle_error_rate_pct`, `error_samples`, `successes_after_first_429` (requests *started* after the first 429), and once throttled `tokens_before_first_429`/`requests_before_first_429`/`seconds_to_first_429`/`concurrency_at_first_429`/`allowed_overshoot`; `until_throttled` bursts add `peak_concurrency`/`required_tokens_per_s`/`limit_reached`/`not_throttled_bound`/`ramp`; step load adds `stages` and `final_stage_{p99_latency_ms,error_rate_pct,throttled_pct}`.
- Available metrics from background MaaS metrics poller (via `shared_state["metrics"]`): raw values as reported by the scenario's `metrics_queries:` block (e.g. `total_requests`, `total_tokens`), plus `{name}_delta` for each (value minus the run's baseline snapshot — see Background Metrics Polling below), plus one entry per `promql`-form assertion (keyed by assertion name). Requires `MAAS_METRICS_URL` in the global ConfigMap and at least one of `metrics_queries:`/a `promql`-form assertion in the scenario.
- Available metrics from `provision_api_key`/`verify_api_key_search` (ADR-019, ADR-021): `shared_state["key_provision_checks"]` (`total_keys`, `name_echo_match_count`, `subscription_checked_count`, `subscription_echo_match_count`, `expected_subscription_checked_count`, `expected_subscription_match_count`, `expires_at_present_count`) and `shared_state["search_check"]` (`found_count`, `expected_count`) — referenced the same way as `inference_results`/`metrics`, e.g. `${harness.key_provision_checks.name_echo_match_count}`. These are entirely REST-derived (no Prometheus/CR involved) but still routed through the `promql` pass-through form for the same parsing-path-coverage reason as every other harness-side-only metric in this codebase.
- Available metrics from `check_platform_health` (REST smoke): `shared_state["platform_health"]` (`model_count`, `api_reachable`). From `check_model_health` (ADR-022/025): `shared_state["model_health"]` counts across the models checked (`models_checked`, `ready_count`, `has_subscription_count`, `has_auth_policy_count`, `gateway_access_label_count`, `rate_limit_enforced_count`, `route_owner_ok_count`, `healthy_count` — the governance counts are omitted, so their checks stay PENDING, if the MaaS catalog can't be read), `["gateway_status"]` (`programmed`), and for a single model `["rate_limit_policy_status"]`/`["http_route_status"]` flags. Also: `shared_state["subscription_limits"]` (`read_subscription_limits`), `["subscription_check"]` (`verify_subscription` tasks), `["pooling"]` (`classify_rate_limit_pooling`), `["direct_probe"]` (`probe_direct_endpoint`).
- **Display fields (ADR-025)** on any dict-form assertion: `label`, `description`, `unit` (shown on the card instead of the raw name/expression — the substituted PromQL moves behind a "Details" toggle), plus a computed human-readable `target` ("100 – 200", "< 5"). `between: [lo, hi]` is a range form for promql-form assertions so a card shows the real value (e.g. 115 tokens) rather than a derived difference; bounds may be simple arithmetic left over from `${config.x}` substitution (`"${config.token_limit} + 100"`), parsed by an AST walker in `harness/result.py`, never `eval`.
- A PENDING assertion never fails a run, so a check whose value only appears on some outcome (e.g. `tokens_before_first_429` never appears if nothing was throttled) is always paired with an always-populated guard (e.g. `rate_limited_count > 0`).
- Run is PASS only if all assertions pass (or no assertions defined)

### Results Storage
- SQLite on PVC at `/data/maaspal.db` (tables: `runs`, `task_results`; `runs` has `config_overrides TEXT` and `duration_ms REAL` columns — `task_results` is unused dead schema, per-task data lives in the progress JSON instead)
- Run results JSON: `/data/results/<run-id>.json` — includes `RunResult.duration_ms` (total run time, `time.monotonic()`-based) and each task's `duration_ms`; `status` can be `PASS`/`FAIL`/`CANCELLED`
- Live assertion state: `/data/results/<run-id>-assertions.json` — written by `emit_assertion_state()`, served by `GET /api/runs/{id}/assertions`
- Live task progress: `/data/results/<run-id>-progress.json` — written by `_write_progress()` at task start/end and on each `emit()`, served by `GET /api/runs/{id}/progress`. Also carries the run page's narration (ADR-025): `findings` (a scenario's conclusion, shown first when present), `metrics_charts` (MaaS-reported vs harness-sent series sampled every metrics poll, declared by a scenario's `metrics_charts:` block), per-task `summary` (tasks set `shared_state["task_summary"]`), `traffic` (one entry per `send_requests`-family `result_key`: `origin` (`pod`/`browser`), `request` (endpoint path) and `stream`, set when the step starts, summary counters, a timeline downsampled to ≤600 points that always keeps status transitions, the chart's limit line — the limit read off the subscription under test, else the scenario's `token_limit` — and `chart`/`chart_group`/`label`/`t0` for how the run page draws it), `resources` (what the run created — names only, never key values or tokens), `tables` (rows a task publishes via `shared_state["_tables"][title]`), and the final `verdict`. Includes a top-level `run_started_at` (wall-clock ISO timestamp) and, per task, `duration_ms` once completed or `started_at` while `RUNNING` — the frontend computes/ticks elapsed time client-side from these rather than the backend pushing a live-updating number.
- Run config snapshot: `/data/results/<run-id>-config.json` — a **scenario-YAML-shaped** snapshot (`name`/`description`/`config`/`metrics_queries`/`tasks`/`assertions`/`cleanup`, built by `_scenario_settings_snapshot()`), meant to be pasted directly into a new `scenarios/*.yaml` file to reproduce the run exactly, not just inspected. `config:` merges the scenario's own declared keys (defaults + any launch-time overrides actually applied) with a small curated set of cluster-level settings (`MAAS_API_URL`, `MAAS_METRICS_URL`, `DEFAULT_MODEL`, `DEFAULT_SUBSCRIPTION`) — deliberately narrower than the raw `_resolved_config` (which is a merge of the *entire* process environment, per `harness/config.py:load_scenario`) so container plumbing (`PATH`, `HOSTNAME`, `KUBERNETES_*`, ...) never shows up. Any dict key matching `(^|_)(token|secret|password)($|_)` at *any* nesting depth (top-level config, or inside a task's resolved `params`) is redacted to `***REDACTED***` — deliberately excludes "key" as a bare substring, since this app's whole domain is provisioning MaaS API *keys* and that false-positived hard on entirely non-sensitive fields (`key_name`, `key_pool`, `total_tokens`, `maas_tokens_match` — the last one being a whole assertion, not just a leaf value, confirmed live). `token_limit`/`token_window` (ADR-024) are a narrower false positive of the same shape — "token" is a complete word in them too, but they're an LLM token budget/time window, not a credential — fixed via an exact-name exception set rather than a pattern change (unlike "key", "token" can't be excluded as a blanket substring without also un-redacting real credentials like `target_token`/`sa_token`). Written once, before the task loop starts (`ScenarioRunner.run()`), so it's viewable from the moment a run begins — served as YAML text (`sort_keys=False`, preserving scenario-file key order) by `GET /api/runs/{id}/config`. Rendered in the UI (`RunSettingsModal.tsx`) via PatternFly's `CodeEditor` (Monaco) in read-only mode — the same component family OpenShift console itself uses for "View YAML" — with built-in copy/download buttons, YAML syntax highlighting, and line numbers, so the output can be copied straight into a new scenario file. See Frontend Monaco Setup below for why it's self-hosted rather than CDN-loaded.
- "Send from user browser" hand-off (ADR-027, `harness/browser_channel.py`): `/data/results/<run-id>-browser-<result_key>.order.json` (targets incl. the run's API keys — mode 0640, deleted when the step ends), `.claim.json` (one tab, `O_EXCL`), `.jsonl` (one raw record per request, then `{"done": true, "reason": …}`; kept, no secrets)
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
- What gets cleaned up: MaaS API keys (individual `DELETE /maas-api/v1/api-keys/{id}`), `MaaSSubscription`s and `MaaSAuthPolicy`s (deleted, or restored if they existed before), throwaway models (`MaaSModelRef` + `LLMInferenceService`), their direct Routes, and minted ServiceAccounts. Each created object is tracked with `record_created()` and shown on the run page with its own cleanup status.

### Stopping a Run (graceful)

See ADR-016 for the full reasoning behind pod-delete-with-grace-period vs. Job-delete vs. `exec`, and why the harness-side signal handler defers to the existing task loop/cleanup rather than a fast-path.

`POST /api/runs/{id}/stop` asks a run to stop — not a raw kill, since the harness has task cleanup (MaaS API key revocation, `MaaSSubscription` CR restoration) that must still run.

- `api/k8s.py:stop_run(run_id)` finds the run's pod by its `maaspal-run-id={run_id}` label, follows its ownerReference to the Job, and **suspends the Job** (`spec.suspend=true`). Kubernetes then deletes the pod with its `terminationGracePeriodSeconds` (`_STOP_GRACE_PERIOD_S`, default 120 s, `MAASPAL_STOP_GRACE_PERIOD_S` override), so the harness's SIGTERM handler still runs cleanup. A suspended Job never starts a replacement pod. Deleting the pod directly (the original ADR-016 approach) was confirmed live to make the Job controller re-run the whole scenario under the same run id. Every Job is also created with `backoffLimit: 0` and the run-id label, so a crashed run never silently re-runs either. Without the `patch jobs` grant it falls back to deleting the pod, with a warning. `api/main.py` deletes a stopped run's suspended Job once the run is finalized, since a suspended Job never completes and its TTL never fires.
- `harness/main.py` registers a `SIGTERM` handler (`loop.add_signal_handler`) that sets an `asyncio.Event`, passed into `ScenarioRunner(..., stop_event=...)`. Without this, the harness has no signal handling at all and a SIGTERM would just kill the process outright, skipping cleanup entirely.
- Inside `ScenarioRunner.run()`: the task loop checks the event before starting each task; the in-flight `task.run(ctx)` itself is raced against the event via `asyncio.wait(..., return_when=FIRST_COMPLETED)` and cancelled if the stop wins, producing a `TaskResult(status="CANCELLED", error="run stopped by user")`; `_settle_and_evaluate`'s poll-sleep and the background metrics poller's sleep both use a small `_interruptible_sleep` helper so a stop wakes them immediately instead of waiting out the interval. The existing `for task in reversed(tasks): await task.cleanup(ctx)` block runs completely unchanged regardless of *why* the loop exited — cleanup already just reads whatever's in `shared_state` at that point (e.g. however many keys were provisioned before cancellation).
- Final `RunResult.status` is `"CANCELLED"` whenever the stop event ends up set, checked once at the very end of `run()` rather than threaded through every early-return path.
- The `POST /api/runs/{id}/stop` route itself does **not** write the DB row when a pod exists (`stop_run` returns `True`) — it relies on the existing `_sync_completed_runs` poller (`api/main.py`) picking up the harness's own final `"CANCELLED"` status from the result JSON on its normal 10s cadence, avoiding a race between the endpoint and the poller. It's a 404 if the run doesn't exist, a 409 if it's already terminal (not `PENDING`/`RUNNING`). Only the no-pod-yet (`PENDING`) case is finalized synchronously by the route, since there's no harness process there to ever self-report.
- **A harness that dies without reporting** (cleanup overruns the grace period and is SIGKILLed, OOMKill) used to leave its run `RUNNING` forever. `api/main.py:_sync_completed_runs` now finalizes such a run: when its Job is suspended (→ `CANCELLED`) or failed (→ `FAIL`), its pod is gone, and no result file exists on two consecutive polls. The cleanup status comes from `-cleanup-status.json`. Also fixed: a live hang where, after a Stop, `_metrics_bg` spun forever without yielding (`_interruptible_sleep` returned instantly once the stop event was set, and there was nothing to query). The run never wrote its result and the pod lingered until SIGKILL. `_interruptible_sleep` now always yields, and the poller exits on stop (regression test `test_stop_with_unresolvable_metrics_checks_still_finishes`).

### SA Permissions Required
- Enough to call the MaaS API with the SA token (the SA must be an owner of the subscriptions scenarios pin keys to — `system:authenticated` on most installs)
- `create`, `get`, `list`, `watch`, `delete`, `patch` on `jobs` in the harness namespace — `patch` is for Stop (suspending the Job, see above)
- `get`, `list`, `watch`, `delete` on `pods` — `delete` is only the fallback Stop uses when it can't patch the Job
- `get` on `pods/log`
- `get`, `list`, `create`, `patch`, `delete` on `maassubscriptions` and `maasauthpolicies` (`maas.opendatahub.io/v1alpha1`, `chart/templates/rbac-maas-subscription-write.yaml`) — for every scenario that creates temporary subscriptions or auth policies
- `get`, `list`, `create`, `patch`, `delete` on `llminferenceservices`/`maasmodelrefs`, and `get`/`create`/`delete` on `routes` (`chart/templates/rbac-model-write.yaml`) — for scenarios that deploy throwaway models (`denied_without_auth_policy`, `gateway_overhead`, `multi_model_load`)
- Cluster-wide read of MaaS/Kuadrant/Gateway API/KServe resources plus `get` on Secrets (`chart/templates/rbac-maas-readonly.yaml`, ADR-017) — the MaaS overview page, `check_model_health`, `read_subscription_limits`, `discover_subscription_models`
- (`tokenratelimitpolicies` read, part of the read-only grant above, is also what `check_model_health` and `provision_subscriptions_distributed`'s readiness wait use)
- `cluster-monitoring-view` ClusterRole binding (`chart/templates/rbac-monitoring.yaml`, cluster-scoped — the only cluster-scoped grant the SA needs beyond its own namespace) — for querying Thanos Querier (background MaaS metrics polling)
- `create`, `delete`, `get`, `list` on `serviceaccounts` and `create` on `serviceaccounts/token`, scoped to the plugin namespace (`chart/templates/rbac-user-provisioning.yaml`, off unless `rbac.userProvisioning=true`) — for `create_user`/`provision_keys_for_users` (ADR-023). **Meaningfully more sensitive than any other grant this harness holds** — minting a ServiceAccount token is a real elevated capability; review deliberately before applying, not as routine.

## Task Reference

- **`provision_api_key`**: Calls `POST /maas-api/v1/api-keys` with the SA token. Stores each created key's full record (`id`, `key`, `name`, `subscription`, `expiresAt`) in `shared_state["api_keys"]`. Supports `count` param to create N keys in a loop; sets `shared_state["task_progress"]` after each key so the UI progress chip updates. **REST-only lifecycle checks** (ADR-019): after each key, compares the response against what was requested and tallies plain-numeric counters into `shared_state["key_provision_checks"]` — `total_keys`, `name_echo_match_count`, `subscription_checked_count`/`subscription_echo_match_count` (only counted when a `subscription` param was actually passed), `expires_at_present_count` — assertable the same way any other metric is. `expect_subscription` (ADR-021, distinct from `subscription` — never sent in the request body) checks auto-selection's *outcome* without forcing it, into `expected_subscription_checked_count`/`expected_subscription_match_count`. Cleanup calls a shared `_revoke_keys()` helper (individual `DELETE /maas-api/v1/api-keys/{id}` per key).

- **`revoke_api_keys`** (ADR-019): Revokes keys from `shared_state["api_keys"]` immediately, mid-scenario, via the same `_revoke_keys()` helper `provision_api_key`'s cleanup uses. `count` revokes only the first N (e.g. 1 of 3, so a later step can check the others still work); revoked keys are flagged `revoked` and their run-page resource marked "revoked". Stores `shared_state["revoked_count"]`. Final cleanup harmlessly re-attempts DELETE (404 = already gone).

- **`verify_api_key_search`** (ADR-019): Calls `POST /maas-api/v1/api-keys/search` with `{"name_prefix": ...}` and counts **active** keys with that prefix into `shared_state["search_check"] = {"found_count", "expected_count"}`. Also registered as `verify_revoked_key_not_searchable`. REST-only; proves this run's keys are findable and filtered correctly, not that a *different* caller's keys are excluded (see the checklist).

- **`send_requests`**: Sends concurrent OpenAI-compatible inference requests. Resolves `url` and `token` via a three-level priority chain: (1) explicit YAML `params`, (2) `shared_state`, (3) `TaskContext` defaults (MaaS model discovery + SA token). Model discovery (`_discover_model`) matches a `model:` param/`DEFAULT_MODEL` against `/v1/models`' `id`, `modelDetails.displayName`, **or `owned_by`** (`"<namespace>/<MaaSModelRef name>"`, confirmed live — the one field that reliably matches a scenario's own `target_model_namespace`/`target_model_name` config; `id`/`displayName` are cosmetic and don't need to resemble the CR name at all). Falling through to "first available" with no match is a real risk once more than one model is registered — confirmed live: an unrelated `ExternalModel` sorting first in the discovery response silently hijacked `rate_limit_validation` (now `verify_subscription_rate_limit`), which had a target model configured for its subscription but never passed it to `send_requests` at all. Every scenario that targets a specific model now passes an explicit `model:` (`"${config.target_model_namespace}/${config.target_model_name}"`, filled by the launch form's model picker) rather than relying on the fallback.

  **Targeting a model `deploy_simulated_model` just created this run (ADR-024)**: confirmed live that such a model is never listed in `/v1/models` — that requires full governance pairing (a `MaaSSubscription` *and* a `MaaSAuthPolicy`), not just `RuntimeReady` — so generic discovery can never resolve it and silently falls back to a *different*, wrong model. `model_from_shared_state` (single target, e.g. `denied_without_auth_policy`, `gateway_overhead`) sets both `params["model"]` (the model's **bare** name) and `params["url"]` directly to that model's own dedicated per-model route (`{MAAS_API_URL}/{namespace}/{name}/v1/...` — auto-created by the LLMInferenceService controller, reachable as soon as `RuntimeReady`, and still enforced by the same gateway `AuthPolicy` as the generic route) before URL/model/token resolution runs — same self-mutating-params style as `key_index` below. For a key pool spanning *multiple* dynamically-deployed models (`multi_model_load`), each key's own `target_model` field (set by `provision_keys_distributed`, `"<namespace>/<name>"` format) gets its own dedicated-path client instead of one shared URL. A third, related case — a key pool with **no** explicit model and **no** per-key `target_model` (auto-selected subscription against an auto-discovered model, e.g. a key pool with no model chosen) — is handled by `_resolve_models_by_subscription`: cross-references each key's own bound `subscription` against `/v1/models`' per-model `subscriptions: [{name}]` list, so an auto-selected subscription and an independently-discovered model can't end up mismatched.

  When `key_pool: true` is set, uses keys from `shared_state["api_keys"]` and distributes requests evenly across the pool (floor(M/N) per key, remainder to first). A `key_index` param (ADR-023) instead targets exactly ONE key from `shared_state["api_keys"]` by position as the task's single dedicated token — bypassing both `key_pool` (whole pool) and a static YAML `token` (can't reference a runtime-created key) — for scenarios needing to run one specific dynamically-created key at a time (e.g. one per user, see `rate_limit_per_user_or_shared.yaml`). A `result_key` param (ADR-023, default `"inference_results"`) writes results to a named `shared_state` slot instead of the one fixed key every `send_requests`-family task has always shared, so two sequential invocations can each keep independent results to compare afterward. After every completed request, updates `shared_state[result_key]` (latency, error/rate-limited/unauthorized counts, throughput, `first_rate_limited_at_tokens` once a 429 happens — ADR-024) and `shared_state["task_progress"]`, then calls `emit_assertion_state()`. Cleanup is a no-op. Also registered under `verify_revoked_key_denied` (`REGISTRY["verify_revoked_key_denied"] = SendRequestsTask`, ADR-019) and `send_requests_as_second_user` (ADR-023) — same class, second/third name, used when a scenario needs another "send some requests" step (e.g. after revoking the key pool, or targeting a second user's key) without colliding with an earlier `send_requests` step's UI chip/progress state (both are keyed by task name — see Task Progress UI below).

- **`check_maas_metrics`**: Optional explicit final metrics check. Runs the scenario's `metrics_queries:` (via `ctx.metrics_queries`, or an explicit `params.queries` override) using the shared `harness/metrics_client.py`, stores raw values in `shared_state["metrics"]`, and prints a human-readable summary to the run log. **This task class is available for explicit use but is no longer included in standard scenario task lists.** MaaS metrics are polled automatically in the background by `ScenarioRunner` (see Background Metrics Polling below).

- **`apply_rate_limit_subscription`**: Uses `kubernetes.client.CustomObjectsApi` to create or patch a `MaaSSubscription` CR (`maas.opendatahub.io/v1alpha1`) with a configured `token_limit`/`token_window`. Stores the original subscription state in `shared_state["original_subscription"]` for cleanup. Cleanup restores or deletes the CR as appropriate. Its get-or-create/patch and restore-or-delete logic is factored into module-level helpers (`_get_existing_subscription`, `_create_or_patch_subscription`, `_cleanup_subscription`, `_subscription_body`) shared with `apply_priority_test_subscriptions` below (ADR-021). After create/patch, polls `status.phase` via `_wait_for_subscription_ready()` (`ready_max_wait_s` param, default 30s) before returning — confirmed live that the K8s API accepting the write doesn't mean the MaaS controller has reconciled it yet, and `provision_api_key` right after would otherwise lose that race with `400 subscription_not_ready` (see ADR-009's third Update). `owner_groups: []` (an explicit empty list) is honored, not silently replaced by the `system:authenticated` default (ADR-023 bugfix — `self.params.get("owner_groups", _DEFAULT_OWNER_GROUPS)`, not `or`). `owner_users_from_shared_state` (ADR-023) appends usernames from a named `shared_state` list (e.g. `"users"`, populated by `create_user`) to `owner_users`, since task `params:` can't reference runtime `shared_state` via YAML templating the way assertions can. `model_from_shared_state` (ADR-018's third Update) reads `model_name`/`model_namespace` from a named `shared_state` list's first entry (e.g. `"deployed_models"`, populated by `deploy_simulated_model`) instead of requiring the literal `model_name`/`model_namespace` params, for scenarios targeting a model created fresh at runtime rather than a static admin-supplied one. `token_window` (default `_DEFAULT_TOKEN_WINDOW = "1s"`) should be set deliberately long (e.g. `"24h"`) for any scenario trying to precisely measure *when* a rate limit first triggers (ADR-024) — a short window both caps achievable sequential demand below the configured limit (so it may never trigger at all) and lets the `openai` SDK's own automatic 429 retry land in a fresh window and quietly succeed, masking a real denial from the harness even though Limitador's own counters show it happened.

- **`apply_priority_test_subscriptions`** (ADR-021): Creates *multiple* `MaaSSubscription`s in one task invocation (a `subscriptions:` list param, each with its own `name`/`priority`/`token_limit`, all sharing one `owner_groups`), tracking them as a list in `shared_state["priority_test_subscriptions"]` so each is cleaned up independently. Exists because two separate YAML entries for `apply_rate_limit_subscription` would silently clobber each other's cleanup state — that task's bookkeeping lives in fixed `shared_state` keys, not namespaced per instance (unlike the `send_requests`/`verify_revoked_key_denied` registry-alias trick in ADR-019, which works precisely because `SendRequestsTask` has no such state to collide). Each subscription individually goes through the same `_wait_for_subscription_ready()` poll as `apply_rate_limit_subscription` before moving to the next.

- **`apply_auth_policy`** (ADR-018): Uses `kubernetes.client.CustomObjectsApi` to create or patch a `MaaSAuthPolicy` CR (`maas.opendatahub.io/v1alpha1`) — the gateway-access half of the two-layer access model (a `MaaSSubscription` alone only grants quota). Same create-or-patch/restore-or-delete shape as `apply_rate_limit_subscription`, `shared_state["original_auth_policy"]`/`"_policy_created"` instead. `denied_without_auth_policy.yaml` uses this task's *absence* to test fail-closed. `model_refs_from_shared_state` covers every model `deploy_simulated_model` created this run in one policy (`multi_model_load.yaml` — confirmed live that a freshly-deployed model has no gateway access at all until a matching policy exists, regardless of subscription/quota), instead of requiring the literal `model_name`/`model_namespace` params.

- **`check_platform_health`**: REST-only smoke check — `GET /v1/models` with the SA token; stores `shared_state["platform_health"]` (`model_count`, `api_reachable`). Used by `smoke_test`.

- **`check_model_health`** (ADR-022/025): Read-only, no cleanup. For one model, or every internally hosted model when `model_name` is blank: reuses `api/maas_client.list_models()` (same image, same SA) for MaaSModelRef Ready / subscriptions / auth policy / namespace gateway-access label, and reads the model's `TokenRateLimitPolicy` and `HTTPRoute` by **label selector** (`maas.opendatahub.io/model=<name>`, `app.kubernetes.io/name=<name>`), plus a named `Gateway` (default `maas-default-gateway`/`openshift-ingress`). Writes `shared_state["model_health"]` counts and a per-model "Model health" table; missing resources count as unhealthy rather than raising. Used by `model_config_health`.

- **`create_user`** (ADR-023): Mints `count` throwaway `ServiceAccount`s in the harness's own namespace, then a `TokenRequest`-issued token for each (`CoreV1Api.create_namespaced_service_account`/`create_namespaced_service_account_token`) — the only caller-identity-minting mechanism buildable from this harness's RBAC without IdP integration. Appends `{name, namespace, username, token}` per user to `shared_state["users"]`, where `username` is the fully-qualified `system:serviceaccount:<ns>:<name>` string confirmed live (ADR-018's Update) to be directly matchable against a `MaaSSubscription`'s `spec.owner.users[]`. Cleanup best-effort deletes each created ServiceAccount, logged, never raises.

- **`provision_keys_for_users`** (ADR-023): Requires `shared_state["users"]` (run `create_user` first). Provisions one MaaS API key per user, authenticated with **that user's own token**, not `ctx.sa_token` — so each key is minted as that caller's identity. All keys pin to one shared `subscription` param. Appends to `shared_state["api_keys"]` (the same list `send_requests`'s `key_pool` reads) with an added `owner_username`; reuses `provision_api_key`'s `key_provision_checks` counters shape unchanged. Cleanup reuses `_revoke_keys()` (deletes via `ctx.sa_token`, the harness's own admin identity, not each key's owning user — an unverified assumption, flagged in ADR-023).

- **`deploy_simulated_model`** (ADR-024): Creates `count` throwaway `LLMInferenceService` CRs using the `llm-d-inference-sim` image (no real model weights — CPU-only, random-length responses), each with a unique run-scoped name (`{name_prefix}-{run_id[:8]}-{i+1}`) so concurrent/back-to-back runs never collide. The controller auto-creates each one's `MaaSModelRef`, `HTTPRoute`, and backing `Deployment`; the task waits for `RuntimeReady` (`ready_max_wait_s`, default 120s) before moving on — **not** full `phase=Ready`, which would deadlock, since that requires a `MaaSSubscription` the *next* task hasn't created yet. `parallel` (default `true`) controls whether models deploy concurrently or one at a time. Appends `{name, namespace, isvc_created, ref_created, ready}` per model to `shared_state["deployed_models"]` — and records it on the run page — **the moment its `LLMInferenceService` is created**, updating the entry as it becomes ready (a Stop during the ready wait cancels the task; recording only at the end left the model behind, confirmed live 2026-10-05), read by `provision_subscriptions_distributed`, `apply_auth_policy`'s `model_refs_from_shared_state`, and `send_requests`'/`apply_rate_limit_subscription`'s `model_from_shared_state`. **Confirmed live such a model is never listed in `GET /v1/models`** regardless of how long you wait — that needs full governance pairing (a subscription *and* an auth policy) — so anything targeting it must use the dedicated-route mechanism described under `send_requests` below, not generic discovery. Cleanup deletes each created `MaaSModelRef` then `LLMInferenceService`.

- **`provision_subscriptions_distributed`** (ADR-024): Creates `subscription_count` `MaaSSubscription`s distributed randomly across `shared_state["deployed_models"]` (1..`max_models_per_subscription` models each, weighted toward underused models for roughly even coverage; `0` = up to all models). Each goes through the same `_wait_for_subscription_ready()` poll as `apply_rate_limit_subscription`, then — **after all subscriptions are created** — a separate wait, `_wait_for_token_rate_limit_policies_ready()`, polls every unique referenced model's `TokenRateLimitPolicy` (same label-selector lookup as `check_model_health`) until both `Accepted` and `Enforced` are `True` (`token_rate_limit_ready_max_wait_s`, default 60s). This second wait exists because subscriptions sharing a model race the controller reconciling that model's single `TokenRateLimitPolicy` (confirmed live: `"the object has been modified"` conflicts) — the subscription's own `status.phase` can be set (even `"Degraded"`) before those races actually finish, so it isn't the signal that gates real traffic. Records (`{name, namespace, original, created, model_refs}`) go in `shared_state["distributed_subscriptions"]`, read by `provision_keys_distributed`; cleanup restores/deletes each independently.

- **`provision_keys_distributed`** (ADR-024): Requires `shared_state["distributed_subscriptions"]`. Creates `key_count` keys spread evenly across those subscriptions (floor(N/M) each, remainder to first). Each key record's `target_model` field is set to `"<namespace>/<name>"` of that subscription's *first* referenced model (not a bare name — `send_requests` needs both pieces to build that model's dedicated route, confirmed live a bare CR name doesn't route correctly). Appends to `shared_state["api_keys"]` (reuses `provision_api_key`'s `key_provision_checks` counters shape). Cleanup reuses `_revoke_keys()`.

- **`read_subscription_limits`** (ADR-025): reads an existing subscription's token limit/window for one model (via `api/maas_client.list_subscriptions()` — same image, same SA) into `shared_state["subscription_limits"]`, so rate-limit checks compare against the real configuration instead of a typed-in number. Fails clearly if the subscription doesn't cover the model or has no limit.

- **`discover_subscription_models` / `send_requests_to_each_model`** (ADR-025): list one subscription's models (with their limits and whether `/v1/models` lists them, into a "Subscription models" table), then send a short burst to each with the scenario's pinned key and fill in the table's Reachable column. Reachability only.

- **`classify_rate_limit_pooling`** (ADR-025): compares two users' `until_throttled` bursts and records a **finding** (`shared_state["_findings"]`, `_verdict_text`): per user, shared, or inconclusive. `shared_state["pooling"]["conclusive"]` is the only pass/fail check.

- **`pause`**: Waits `duration_s` (seconds or a window string like `"1m"`) + `buffer_s`, capped at `max_s` (default 900; capped waits are called out). Reports per-second progress. A graceful stop interrupts it like any task.

- **`send_requests` additions (ADR-025)**:
  - **Bursts**: `retries` (OpenAI SDK `max_retries`, default 2; rate-limit scenarios use 0 so a hidden retry can't mask or land past a 429), `stop_after_429s`, `key_pool_filter: active|revoked`, round-robin key-pool ordering, `skip_unless: "ns.key"` (skip when an earlier precondition failed).
  - **`until_throttled`**: send until MaaS throttles, ramping concurrency (1 → 2 → 4 …, every 2 s, up to `max_concurrency`, default 128) while the measured token rate is below `limit ÷ window` (a fixed window resets otherwise). The limit and window come from `limit`/`window` params or `limit_from_shared_state`/`window_from_shared_state`; bounds are `max_duration_s` (default one window, at most 600 s) and `max_requests` (default 20,000) — scenario settings, not constants. If never throttled, `_diagnose_unthrottled` names the bottleneck (`response`: more concurrency stopped helping; `send`: still scaling at the ceiling; `time`/`requests`: a budget ran out) as a finding and verdict with the numbers. Progress tracks tokens toward the limit. `allowed_overshoot` = concurrency at the first 429 × max tokens per request bounds "throttled at the limit".
  - **Step load** (`stages: "5,10,25,50"` + `stage_duration_s`): continuous traffic at each concurrency level, reported per step (req/s, tokens/s, p50/p95/p99, error and throttled %), plus `final_stage_*` for checks; progress shows the step.
  - **Run page**: `show_limit: false` (no limit line — for a load test's deliberately unlimited subscription, which would flatten the chart), `chart` (open as a chart rather than a one-line summary), `chart_group`/`label` (several bursts on one timeline, placed by a wall-clock `t0`), `error_samples` (top 3 non-429 failure reasons, exactly as received — status line plus the whole body, never shortened or reworded (`_error_message()`) — incl. the cause the SDK hides behind "Connection error.", each with its `median_ms`/`p10_ms`/`p90_ms` — a tight cluster is a timeout's signature). `failed_attempts`/`retried_failed_attempts`/`attempt_error_samples` count every failed HTTP attempt via an httpx event hook, including ones the SDK retried away (per step too: `http_attempts`, `failed_attempts_pct`). In step-load mode, any non-429 failure — visible or retried — also sets `shared_state["_verdict_note"]`, which `_render_verdict` appends even to a passing verdict, 404 as its own outcome.
  - **Direct calls**: `url_from_shared_state` reads a deployed model's `direct_url` (set by `expose_model_route`); `insecure_tls` for its self-signed certificate.
  - Aliases: `send_requests_after_window`, `send_requests_via_maas`, `verify_other_keys_still_work`, `verify_revoked_key_denied`, `send_requests_as_second_user`.

- **Request API / Streaming** (`send_requests`): `api: chat_completions|completions|responses|embeddings` (default chat), `stream` (asks for a final usage chunk, reads the stream to the end, records `p50/p95_ttft_ms`; ignored for embeddings), `batch_size` (> 1 sends a list of prompts to completions/embeddings). Both fall back to `ctx.config["request_api"]`/`["stream"]`, so `send_requests_to_each_model` follows them too. Every scenario with a send-requests-family step declares `request_api: chat_completions` + `stream: false` in `config:`, as advanced `inputs` (`choice_labels` shows endpoint paths; a boolean default renders as a checkbox), and passes `api: "${config.request_api}"`/`stream: "${config.stream}"` to each such step — enforced by `test_send_requests_steps_follow_request_api_settings`. Results add `api`, `stream`, `p50_ms_per_output_token`, for streamed replies `p50_tpot_ms`/`p50_itl_ms`/`p95_itl_ms`/`p50_stream_spread_ms`/`p50_stream_chunks` and `streamed_reply_count`/`all_at_once_reply_count` (text chunks spread < `STREAM_SPREAD_MIN_MS` = all at once; `delivery_text()`), `usage_missing_count` (replies with no usage; their tokens are not guessed) and `failure_origins`/`failure_origin_evidence` (`failure_origin()`: who answered a failed request — `gateway` from `x-ext-auth-reason`/429/an empty body with no `server` header, `model` from the model server's own `server` header (`fasthttp`, `uvicorn`) or a vLLM-style JSON error body; confirmed live 2026-10-05). Streaming through MaaS is token-counted even when a client omits `include_usage`: MaaS's payload-processing `stream-usage-enforcer` adds it (confirmed live).

- **Send from user browser** (`send_requests` `from_browser`, else config `send_from_browser`; ADR-027): instead of sending, the step writes an order (targets with the run's own keys, the round-robin `plan`, api/stream/prompt/retries/timeout/stop rules) for the open run page, waits for a claim (`browser_claim_timeout_s`, default 120), then tails the browser's raw records into `record_outcome()` — the same per-request bookkeeping as pod-sent requests (`_Outcome`), so results, checks, charts, logs (`[send_requests] (browser) …`) and metrics are unchanged; traffic carries `origin: "browser"`. Fails the step when no page claims it, the tab goes quiet (`browser_idle_timeout_s`, default 60), or the first request gets no readable answer (`blocked`: CORS or unreachable — reported as the result with a "Blocked by the browser: no readable answer from <host> (<browser error>) — …" finding, verdict and log line, never fixed cluster-side). Records and log lines keep what the browser got exactly as received (whole body, the browser's own error); the BFF puts no length limit on them. Never hands out the SA token. Fixed bursts only: `until_throttled`/`stages`/`insecure_tls`/`url_from_shared_state` raise. Every scenario with a send-requests step declares `send_from_browser` as an advanced boolean input and passes `from_browser: "${config.send_from_browser}"` to each such step, defaulting to `false` wherever a step uses `until_throttled`/`stages` (`test_send_requests_steps_offer_send_from_browser`; `gateway_overhead`/`request_types` exempt). On by default: `smoke_test`, `verify_subscription`, `api_key_lifecycle`, `denied_without_auth_policy`, `usage_metrics_accuracy`. `send_requests_to_each_model` forwards it and stops at the first failed burst.

- **`send_requests_each_type`** (`harness/tasks/request_types.py`): for each selected type (`types`, blank = all: chat, completions, responses — each plain and streamed —, completions batched, embeddings, and plain-HTTP probes for vLLM's `/v1/messages`, `/tokenize`, `/v1/rerank`) sends `requests_per_type` requests through MaaS with the scenario's key and, when `compare_direct` and the model's in-cluster workload Service (`<name>-kserve-workload-svc.<ns>.svc:8000`, no Route, nothing created) answers, the same requests directly using the model server's own name from its `/v1/models` (MaaS's id, e.g. `publishers/llm/models/…`, is rewritten by the gateway and unknown to the model). Every type, probes included, gets a traffic card. `attribute()` decides **Failed at**: works direct but not via MaaS → MaaS gateway; fails both with a real HTTP answer → model; direct unusable → the MaaS responses' `failure_origins`, marked "(likely)". Per-request `request_timeout_s` (default 30) and `stop_after_transport_errors: 1` (a `send_requests` param), because the MaaS gateway holds a model's *error* reply ~60 s then drops it for every subscription but the one whose limit sorts last in the model's TokenRateLimitPolicy (confirmed live, see the checklist) — such a type reads "model (and MaaS hung)". Per token: streamed → TPOT ((last − first text chunk) ÷ (output tokens − 1)) plus ITL p50/p95 from the real chunk gaps; not streamed → only an upper bound (latency ÷ tokens). Never fails on a type: writes `shared_state["request_types"]` counts, a "Request types" table, a finding and `_verdict_text`.

- **`expose_model_route` / `probe_direct_endpoint`** (`harness/tasks/model.py`): a passthrough-TLS OpenShift Route straight to a just-deployed model's workload Service (`<name>-kserve-workload-svc`, port `https`), so a latency comparison enters through cluster ingress both ways. `LLMInferenceService.status.addresses` only ever lists MaaS gateway URLs (confirmed live). The probe checks the route answers before timing, records `direct_probe.reachable`, and never fails the run. If unreachable it records a finding, and the MaaS leg still runs. Needs `routes` in `chart/templates/rbac-model-write.yaml`.

- **Per-resource cleanup status (ADR-025)**: tasks call `harness/tasks/base.py:record_created(ctx, task, kind, name, existed=…)` for every cluster object they create or patch (names only — never key values/tokens). The runner marks each with its owning task's `cleanup()` outcome (`removed`/`restored`/`cleanup failed`, or `left in place` with auto cleanup off; mid-run `revoked` for keys), via `harness/cleanup_state.py`, which `api/cleanup.py`'s manual "Clean Up Now" also uses.

### Background Metrics Polling

MaaS/RHOAI metrics are read from Prometheus/Thanos Querier's instant-query API (`GET {MAAS_METRICS_URL}?query=<promql>`), not a MaaS-specific REST endpoint — see ADR-014 for why, and the SA RBAC (`chart/templates/rbac-monitoring.yaml`, `cluster-monitoring-view`) this requires. A scenario's `metrics_queries:` block (a dict of `{name: promql}` resolved for `${config.x}` like `assertions:`/task `params:`, see `scenarios/usage_metrics_accuracy.yaml`) names which named PromQL queries to run — moved here from the global ConfigMap's `MAAS_METRICS_QUERIES` in ADR-015 so the query text lives next to the assertions that use it; `MAAS_METRICS_URL` itself stays global (cluster wiring). Originally confirmed on `cluster-rkmhx.rkmhx.sandbox1230.opentlc.com`; the chart's global ConfigMap (formerly `deploy/configmap-global.yaml`) last targeted `cluster-2ppnp.2ppnp.sandbox449.opentlc.com`, where `limited_calls` was re-confirmed live on 2026-10-02. Kuadrant/Limitador's gateway counters `authorized_calls` (total_requests) and `authorized_hits` (total_tokens — weighted per-token via the model's `TokenRateLimitPolicy`), both scoped by the `limitador_namespace` label (the target model's HTTPRoute name). See `docs/architecture/maas-metrics-reference.md` for the full catalog of every metric-emitting component found (not just these two) and ADR-014 for the decision. If deploying to a different cluster/model, re-verify these against a live `/api/v1/series` query rather than assuming — metric names/labels are confirmed to vary across Limitador deployments.

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

The `TaskProgress` component polls `GET /api/runs/{id}/progress` every 2 s, independently of logs and assertions. Chips also accept open-ended progress (`total: null`) and a `unit` ("57 / 100 tokens", "step 2 / 4").

Below the chips, `RunDetail.tsx` renders the rest of the page from the same progress file (`components/RunInsights.tsx`):
- **Verdict banner**, or a **Finding** card instead when the scenario found something out rather than checked an expectation.
- **"What happened"** (`RunSteps`): each task's one-line `summary` with the objects that task created and each one's cleanup status. A step that sent requests carries a **Sent from the pod / Sent from the browser** label and an endpoint label (`/v1/chat/completions · streamed`, or `N request types`), from its traffic records (ADR-027). Five or more objects of one kind collapse into a count-per-status row with "Show all". It ends with a Cleanup row, and the whole panel can be collapsed.
- **Traffic**: per burst, a one-line summary or the full panel (stats, error reasons, `TrafficChart.tsx`), switchable either way ("Show chart" / "Show summary"). Bursts in one `chart_group` share a timeline with the waits between them shaded. Step-load bursts show a per-step table plus throughput and p95-by-step charts. `MetricsComparisonChart.tsx` plots MaaS-reported vs sent counts.
- **Tables** tasks publish, the **checks** (`AssertionPanel.tsx`) on the right, and a **Logs** panel at the bottom: line count, last-line preview, a Show/Hide button, open automatically on failure.

The dashboard owns the page chrome (masthead, sidebar, scroll container), so the plugin renders no `<Page>` of its own — only the required `CommunityBanner`, a "MaaS:PAL" header (with a mascot logo that changes on every page, from a light- or dark-theme set — `src/app/logos.ts`) and its routes. Scenarios, Runs and MaaS overview each open with a title and one-line explanation (`components/PageIntro.tsx`).

### Scenario Categories (ADR-020, regrouped by ADR-025)

The **Scenarios** page (`ScenarioCatalog.tsx`) is a catalog: a left rail with search, categories (with counts), Source (Custom / Built-in, shown only when custom scenarios exist) and Type (uses your setup / creates temporary resources / read-only) filters, all kept in the URL so Back from a run restores them, and a card gallery grouped by category. The categories are organised by the user's question: Quick check, Rate limits, Access control, API keys, Usage metrics, Performance, Diagnostics. **Custom**: `GET /api/scenarios` sends `custom: true` for any scenario not in `BUILTIN_SCENARIOS` (`api/routes/scenarios.py`). A custom scenario carries a Custom label; if its `category:` is a built-in one it's listed there ahead of the built-ins, otherwise it goes in the Custom category, which comes first and only appears when non-empty. Within a category: custom first, then `kind: verify` before `explore`, then `order`. Cards show `title`, `summary` and badges (custom, uses your setup / creates temporary resources / read-only, needs extra RBAC, duration); clicking a card (or Enter/Space on it) opens the launch form — no separate Run button. The category order lives in two places kept in sync by hand — `CATEGORY_ORDER` in `src/app/components/ScenarioCatalog.tsx` and `KNOWN_CATEGORIES` in `bff/api/routes/scenarios.py` (used by `harness/tests/test_scenarios.py`). Adding a built-in scenario means adding its name to `BUILTIN_SCENARIOS` too (`test_every_builtin_scenario_has_a_file` checks the other direction).

### UI colours (light + dark)

Every colour is a token in `src/app/styles/tokens.css`, with a value per dashboard theme (dark = `pf-v6-theme-dark` on `<html>`). Neutrals alias PatternFly's semantic tokens so panels match the host surface; brand/status/chart colours are MaaS:PAL's own. Components use `var(--maaspal-…)` in CSS and `COLOR`/`toneColor`/`toneBg` (`styles/colors.ts`) in TSX; statuses go through `src/app/status.ts`. `src/app/styles/noHardcodedColors.test.ts` fails on any colour literal elsewhere. Palette, status mapping, component guide and how to check both themes: [`docs/design/colors-and-components.md`](docs/design/colors-and-components.md).

### Frontend Monaco Setup

`RunSettingsModal.tsx`'s and the MaaS overview pages' read-only YAML views (`RawYamlModal.tsx`, see Results Storage above) are the only places this app uses Monaco. Deliberate choices in `src/app/monacoSetup.ts`, because the plugin bundles everything into its own image and avoids external runtime dependencies:
- **Self-hosted, not CDN-loaded.** `@monaco-editor/react` defaults to lazy-fetching Monaco's AMD bundle from a public CDN at runtime — a real risk for an app meant to run inside OpenShift clusters that may have restricted egress. `monacoSetup.ts` imports `monaco-editor` directly and points `@monaco-editor/react`'s `loader.config({ monaco })` at it instead, plus configures `self.MonacoEnvironment.getWorker` to use a webpack-bundled worker (`new Worker(new URL('monaco-editor/editor/editor.worker.js', import.meta.url))`, webpack 5's native worker syntax) rather than one Monaco would otherwise fetch itself. With `publicPath: 'auto'`, the worker and chunks are served from the plugin's own path (`/_mf/maaspal/` inside the dashboard).
- **Trimmed to YAML only.** Importing the full `monaco-editor` package entry pulls in tokenizers for every one of its ~80 bundled languages (pushed the lazy chunk over 4MB) when this app only ever displays YAML. `monacoSetup.ts` instead imports the slim core (`monaco-editor/editor/editor.api.js`) plus just the YAML language definition (`monaco-editor/languages/definitions/yaml/register.js`) — both resolved through `monaco-editor`'s package.json `exports` map, which already implies the `esm/vs/` path prefix (a doubled-prefix import path is a common mistake here). `tsconfig.json` uses `"moduleResolution": "bundler"` so TypeScript follows that map too.
- `RawYamlModal` is loaded via `React.lazy()` everywhere (not a static import) so Monaco's bundle is only fetched when a user actually opens a YAML view — confirmed in the build: the app chunks contain no Monaco code.
- `RawYamlModal` uses PatternFly 6's composable `Modal` (`ModalHeader`/`ModalBody`/`ModalFooter`) and `@patternfly/react-code-editor` v6.

## Scenarios

Every scenario answers one question of the form "is my MaaS behaving the way I expect, given what I set up?" (ADR-025). `kind: verify` scenarios use the cluster's existing setup and create nothing but API keys; `kind: explore` scenarios build temporary models/subscriptions/identities to probe how MaaS itself behaves. Every `send_requests`-family assertion goes through the `promql` pass-through form (ADR-015), even pure harness-side numbers — that exercises the real Thanos query path on every run, it doesn't make those checks more meaningful. Rate-limit scenarios send with `retries: 0` and `until_throttled`, ramping concurrency only as far as needed to use the limit up within one window.

| Scenario (Category, kind) | Question | Key checks | Notes |
|---|---|---|---|
| `smoke_test` (Quick check, verify) | Is MaaS working end to end? | models listed ≥ 1; key created; error rate < 5% | REST + gateway only. Revocation/search moved to `api_key_lifecycle`. Formerly `platform_health_check`. |
| `request_types` (Quick check, explore) | Which request types does my model support? | none — the Request types table and finding are the answer (a failing type never fails the run) | Own temporary unlimited subscription (1,000,000,000 tokens / 1 s, like `load_test`) and a key pinned to it, so throttling can't make a type look unsupported. `send_requests_each_type`: every type N times via MaaS and directly; a "Request types" table with Failed at (MaaS gateway / model / likely), time per token, and Delivery (streamed vs. all at once, and whether MaaS or the model held a stream back). Default prompt asks for a ~300-word story so streams have enough tokens to show pacing. |
| `verify_subscription` (Quick check, verify) | Does my subscription work for every model it covers? | covers ≥ 1 model; key bound to it; models that didn't answer == 0 | Visible steps: `discover_subscription_models` → `provision_api_key` (pinned) → `send_requests_to_each_model`. Reachability only — limits are the Rate limits scenarios' job. |
| `verify_subscription_rate_limit` (Rate limits, verify) | Is my subscription's rate limit enforced? | throttled at all; `tokens_before_first_429` between the subscription's own limit and limit + `allowed_overshoot`; successes after first 429 == 0; Limitador `limited_calls` delta > 0 | Existing subscription only. `read_subscription_limits` reads the real limit and window; `until_throttled` ramps concurrency to use it up within one window. Max concurrency / duration / requests are advanced settings; if the limit can't be reached, a finding names the bottleneck. **Confirmed live 2026-10-02** on `simulator-premium` (100,000 / 1m): throttled at 100,009 tokens at concurrency 4. Merged from `rate_limit_validation` + `_existing_subscription`. |
| `keys_share_user_budget` (Rate limits, explore) | Do all my keys share one budget? | combined tokens before first 429 within [limit, limit + `allowed_overshoot`]; no key succeeds after the first 429 | Own temporary subscription (100 tokens / 24h), N keys round-robin, until throttled. Not yet run live. |
| `rate_limit_window_recovery` (Rate limits, explore) | Does access come back after the window? | throttled before the wait; successes after the wait > 0 | Own temporary subscription (50 tokens / 1m), `pause` for the window + buffer; both bursts on one chart. Not yet run live. |
| `subscription_auto_selection` (Rate limits, explore) | Which subscription do my keys get? | auto-selected key bound to the higher-priority subscription; tokens sent > low limit; 429s == 0 | Two temporary subscriptions (priority 50/10 tokens vs 200/1M). Formerly `rate_limit_priority_precedence` (ADR-021). |
| `rate_limit_per_user_or_shared` (Rate limits, explore) | Are limits per user or shared? | result conclusive | Reports a **finding** (per user / shared / inconclusive) via `classify_rate_limit_pooling` — no expectation input. Two minted ServiceAccounts on one temporary 50-token subscription, both bursts `until_throttled`. Needs `chart/templates/rbac-user-provisioning.yaml`. |
| `denied_without_auth_policy` (Access control, explore) | No auth policy → access denied? | > 90% rejected; 401/403 > 0; 429 == 0 | Throwaway model + identity + quota-only subscription. Formerly `subscription_without_authpolicy` (ADR-018). |
| `api_key_lifecycle` (API keys, verify) | Do API keys behave correctly? | name echo / expiry / search finds all; error rate < 5%; revoke 1 key → it's denied, the others still work (< 5% errors), search lists key_count − 1 active | REST-only (ADR-019). |
| `usage_metrics_accuracy` (Usage metrics, verify) | Do MaaS usage metrics match real traffic? | `authorized_calls`/`authorized_hits` baseline-deltas match requests/tokens sent within tolerance | `limitador_namespace` autofilled from the model's HTTPRoute. Formerly `metrics_fill` (ADR-014/015). Run page plots MaaS-reported vs sent (`metrics_charts:`). |
| `load_test` (Performance, explore) | How does MaaS hold up under load? | error rate (excluding 429s), throttled % (should be 0) and p99 at the heaviest step | Creates its own temporary subscription for the chosen model (1,000,000,000 tokens per 1 s, so quota never throttles it), then a step load: concurrency `5, 10, 25, 50` × 30 s by default (editable); per-step table plus throughput and p95-by-step charts. Merged from `single_key_load` + `multi_key_load`. Keeps the SDK's normal retries (part of the real client experience) but reports what they hid: retried failed attempts with what they got back, and a per-step "Failed attempts" column (live 2026-10-02: under load the MaaS gateway's 200 ms Authorino timeout returned 1,458 500s, of which callers saw only 77 — see the checklist's Networking section). No limit line on its chart. |
| `gateway_overhead` (Performance, explore) | How much latency does the gateway add? | error rates; median via MaaS − median direct < `max_overhead_ms` | Deploys its own simulated model with an auth policy and subscription, plus a passthrough Route straight to it (`expose_model_route`). `probe_direct_endpoint` checks the route answers, then the same burst runs both ways. No endpoint inputs. **Confirmed live 2026-10-02**: median 7 ms direct vs 48 ms through MaaS. Formerly `direct_inference`. |
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

### Development
1. **Unit tests**: `make test` — pytest in `bff/` (harness + API, mocked HTTP/K8s; `api/tests/test_auth.py` covers the access gate) and Jest (UI). `make lint` — eslint + markdownlint, ruff, `helm lint`. `make typecheck` — tsc, mypy. `make validate` runs all of them. Python targets use `PYTHON=` (a virtualenv with `make install-bff`).
2. **Scenario files**: `bff/harness/tests/test_scenarios.py` checks every scenario loads and resolves (in every `when:` mode), uses registered tasks, a known category, display metadata, and `inputs`/`requires`/`show_if` that reference real config keys, unique `previous_names`. Scenario `config:` defaults may reference cluster settings (`model_namespace: "${config.NAMESPACE}"`), resolved by `harness/config.py:resolve_config_defaults` for both the harness and `GET /api/scenarios`.
3. **Local dev**: `make dev-bff` (FastAPI on :3000, access check off) plus `make dev-standalone` (webpack on :9500, open `/maaspal`) — or `make dev` with a local RHOAI dashboard on :8443. **Local harness**: `cd bff && python -m harness.main --scenario scenarios/smoke_test.yaml --run-id test-123` (needs real cluster env vars).
4. **Seeded run pages**: to check the run page without a cluster, run `ScenarioRunner` on a real scenario with only the cluster edges faked (key creation, model responses, Thanos), point `DATA_DIR`/`DB_PATH` at a scratch dir, serve with `make dev-bff` + `make dev-standalone`, and screenshot with headless Chrome. This caught several layout and wording bugs that tests didn't.
5. **Plugin build**: `npm run build` must produce `dist/remoteEntry.js` exposing `./extensions` and `./Icon`. `helm template maaspal chart/ -n cp-maaspal` must render (the MaaS/Thanos URLs are empty there — `lookup` needs a cluster).
6. **Deploy**: `make image-push REGISTRY=… VERSION=…` then `make deploy REGISTRY=… IMAGE_TAG=…` (Helm), then register with the dashboard (README step 2). Changes reach the cluster only through new images.

### Live checks per scenario
Run from the dashboard's MaaS:PAL pages (or `POST /api/runs` through the dashboard proxy) against a cluster with the chart installed and the plugin registered. "Confirmed" means the current scenario passed live; "earlier form" means an older version passed live but the scenario has changed since (ADR-025).

| Scenario | Check live | Status |
|---|---|---|
| `smoke_test` | models listed, key created, inference < 5% errors | earlier form (as `platform_health_check`) |
| `verify_subscription` | pick a subscription: table lists its models, each answers | not run live |
| `request_types` | table fills for every type; direct comparison reachable (or the reason it isn't); Failed at matches what `curl` shows via MaaS and direct | not run live |
| `verify_subscription_rate_limit` | throttled within [limit, limit + overshoot], 0 successes after, Limitador denials = harness 429s; on a huge limit, an "Inconclusive" finding naming the bottleneck | **confirmed 2026-10-02** (`simulator-premium`, 100k/1m: 100,009 tokens, concurrency 4, 6 denials = 6 429s) |
| `keys_share_user_budget` | combined tokens at first 429 ≈ one limit; no key gets through after | not run live |
| `rate_limit_window_recovery` | throttled, wait, access back; one chart with the wait shaded | not run live |
| `subscription_auto_selection` | key auto-bound to the high-priority subscription; no throttling past the low limit | earlier form (as `rate_limit_priority_precedence`) |
| `rate_limit_per_user_or_shared` | needs `rbac-user-provisioning.yaml`; a conclusive finding (per user or shared) | earlier form (expectation-based) |
| `denied_without_auth_policy` | > 90% denied as 401/403, 0 throttled; no orphaned model/SA/subscription (`oc get llminferenceservice,maasmodelref -n maaspal`) | earlier form (as `subscription_without_authpolicy`) |
| `api_key_lifecycle` | revoke 1 of 3 → it's denied, the other 2 still work, search lists 2 active | not run live in this form |
| `usage_metrics_accuracy` | `maas_requests_match`/`maas_tokens_match` pass; the MaaS-vs-sent charts converge after a scrape | not run live end to end since the `clamp_min(vector(...))` fix |
| `load_test` | own unlimited subscription; step table and charts; 0 throttled at the top step | **partly confirmed 2026-10-02** (stopped mid-run by design: 6,434 requests, 0 throttled) |
| `gateway_overhead` | route probe passes; direct vs MaaS latency compared; route and model removed | **confirmed 2026-10-05 as a plugin** (7 ms direct vs 51 ms via MaaS; model in `cp-maaspal`; all 5 resources removed) |
| `multi_model_load` | every request lands on its intended model; nothing orphaned | earlier form (as `multi_model_full_load`) |
| `model_config_health` | all models checked; flags match `oc get tokenratelimitpolicy,gateway,httproute`; fails honestly when a resource is missing | earlier form (as `platform_health_check` with 4 flags) |

### Cross-cutting live checks
- **Stop** (**confirmed 2026-10-02** on `load_test`): the pod is gone within seconds, the run shows `CANCELLED` and stays there, the Job is deleted after finalization, and temporary resources are removed. Needs `patch` on Jobs (`chart/templates/rbac.yaml`). A run whose harness dies without a result is finalized by the API backstop (confirmed: it finalized a previously stuck run).
- **Cleanup**: run page lists every created object as removed/restored; with auto cleanup off they show "left in place", then "removed ✓" after Clean Up Now. Spot-check with `oc get maassubscriptions -n models-as-a-service` and the MaaS key search.
- **As a dashboard plugin** (**confirmed 2026-10-05**, RHOAI 3.5.1, dashboard at `rh-ai.<apps domain>` behind `data-science-gateway`): `/_mf/maaspal/` serves `remoteEntry.js` and every chunk incl. Monaco's worker; `/maaspal/api/*` reaches the BFF with the user's token; no token → OpenShift login redirect; an identity without `maaspal-user` gets the 403 detail on every API route, and gets in once bound; every MaaS overview section `available: true`; from another namespace the BFF times out (NetworkPolicy) while the frontend answers. The `MODULE_FEDERATION_CONFIG` env override survived the first operator reconcile.
- **UI**: sidebar shows Scenarios / Runs / MaaS overview, and `/maaspal/setup` redirects to the overview; the Scenarios catalog's filters survive Back from a run, cards are the same width under All as in one category (a page scrollbar used to change the column count), custom scenarios carry a Custom label and come first; the header logo changes per page and switches set with the dashboard theme (**catalog and header seen live 2026-10-05**; the card-width fix and the overview rename only locally so far); single page scrollbar; launch form shows ⓘ help, an Advanced section, "What this run will do" with "More details", autofilled limit/window/route from the pickers; run history shows titles, including for runs of renamed scenarios.
- **Send from user browser** (ADR-027, **not run live**): `smoke_test` with it on — DevTools shows the requests going to `maas.<apps>` from the page, the run page banner counts them, the traffic card says "(sent from the browser)", log lines are tagged `(browser)`. If the gateway refuses the cross-origin call, the step fails with the "no readable answer" finding — record that in the checklist as the result. Closing the tab mid-step fails it after the idle timeout.
- **CR-based scenarios** need `harness/main.py:_load_kube_config()` (ADR-009 update) — without it every `CustomObjectsApi` call fails with `LocationValueError: No host specified`. Fixed and confirmed live; if CR tasks start failing that way again, check this first.
