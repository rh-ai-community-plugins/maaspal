# MaaS:PAL — Implementation Plan

> **Status (2026-10-02):** this is the original phased build plan, kept as a record. Phases 0–7 are built and running on a live cluster. Phase 8 exists as `.github/workflows/ci.yml` (lint, type check, unit tests). Phase 9's separate guides (quickstart, authoring guide, runbook) haven't been written; `README.md` and `CLAUDE.md` cover the essentials. The project has also grown well past the original five scenarios. Scenario names here are the originals: `single_key_load`/`multi_key_load` are now `load_test`; `direct_inference` is `gateway_overhead`; `rate_limit_validation` is `verify_subscription_rate_limit`; `metrics_fill` is `usage_metrics_accuracy` (ADR-025 has the full mapping). Log streaming moved from SSE to REST polling (ADR-007). For the current state of the project, see `CLAUDE.md`. For what still needs checking against a live cluster, see `docs/architecture/empirical-verification-checklist.md`.

## Overview

This document breaks the MaaS:PAL project into discrete, ordered phases. Each phase has a clear scope, a set of deliverables, and a **Verification** section — the exact commands or steps that confirm the phase milestone is met before moving on.

Cross-cutting concerns (documentation, CI, deployment manifests) are given their own phases rather than being treated as afterthoughts bolted onto feature phases.

---

## Phase Summary

| Phase | Name | Key Deliverable | Milestone |
|---|---|---|---|
| 0 | Project Foundation | Repo scaffold, dev tooling, CI skeleton | Repo builds and lints on CI |
| 1 | Harness Core | Task engine, config loader, assertion evaluator | Scenario runs locally against mock MaaS |
| 2 | Task Implementations | All four task classes with unit tests | All tasks pass unit tests with mocked HTTP |
| 3 | Scenario YAMLs | All five scenario definitions | Scenarios load, validate, and resolve config |
| 4 | API Server | FastAPI backend — runs, scenarios, SSE logs | API serves scenarios and streams job logs |
| 5 | Frontend | React + PatternFly UI with live assertion panel | Full UI works end-to-end in development |
| 6 | Containerisation & Manifests | Dockerfile, deploy manifests, Makefile | One-command deploy to an OCP cluster |
| 7 | Integration & E2E Testing | Tests against a real RHOAI environment | All 5 scenarios pass on a live cluster |
| 8 | CI/CD Pipeline | Full automated pipeline: lint, test, build, push | CI green on every PR and merge to main |
| 9 | Documentation | README, guides, runbook, API reference | Docs reviewed and published |

---

## Phase 0 — Project Foundation

**Goal**: establish the repository structure, development tooling, and CI skeleton so all subsequent phases have a consistent base to build on.

### Deliverables

- `pyproject.toml` — Python dependencies declared: `fastapi`, `uvicorn`, `kubernetes`, `aiosqlite`, `httpx`, `openai`, `pytest`, `pytest-asyncio`
- `ui/package.json` — React, TypeScript, PatternFly, Vite, ESLint, Jest
- `.github/workflows/ci.yml` (skeleton) — lint + placeholder test job; triggers on PR and push to `main`
- `Makefile` — initial targets: `lint`, `test`, `build`, `dev`
- `.gitignore`, `pyproject.toml` `[tool.ruff]` / `[tool.mypy]` config stubs
- `docs/architecture/adrs/` — all ADRs from design phase (already done)
- `CLAUDE.md` — finalised design document (already done)

### Verification

```bash
# Python linters pass with zero findings
make lint

# Placeholder import-smoke tests pass
make test

# FastAPI dev server starts (all routes return 404 — expected)
uvicorn api.main:app --port 8000
curl http://localhost:8000/nonexistent   # expect HTTP 404, not a crash
```

Post-phase checklist:
- [ ] `make lint` completes with no warnings or errors printed
- [ ] `make test` output shows `2 passed` (or more) with no failures
- [ ] `uvicorn api.main:app` starts without any import errors in the terminal
- [ ] `curl http://localhost:8000/nonexistent` returns `{"detail":"Not Found"}`, not a Python traceback

### Dependencies

None — this is the starting point.

---

## Phase 1 — Harness Core

**Goal**: implement the task engine, config loader, assertion evaluator, and scenario runner without any real task logic. A scenario can be executed end-to-end using stub tasks.

### Deliverables

- `harness/tasks/base.py` — `Task` ABC (`run`, `cleanup`), `TaskContext` dataclass, `emit_assertion_state()` helper
- `harness/tasks/registry.py` — task name → class mapping; raises on unknown task names
- `harness/config.py` — two-level config merge (env vars from global ConfigMap + scenario YAML `config:` section); `${config.<key>}` interpolation in params and assertion values
- `harness/result.py` — `RunResult` dataclass; assertion evaluation engine (pending / passing / failing states, operator parsing)
- `harness/runner.py` — `ScenarioRunner`: loads scenario YAML, instantiates tasks from registry, calls `run()` in order, calls `emit_assertion_state()` after each task, calls `cleanup()` on all tasks in reverse order after scenario completes (pass or fail)
- `harness/main.py` — CLI entrypoint: `python -m harness.main --scenario <name> --run-id <uuid>`; writes `RunResult` to `/data/results/<run-id>.json`
- `harness/tests/test_config.py` — unit tests: interpolation, missing keys, override precedence
- `harness/tests/test_result.py` — unit tests: all operator types, pending detection, PASS/FAIL logic
- `harness/tests/test_runner.py` — unit tests: task ordering, cleanup on failure, assertion state progression using stub tasks

### Verification

```bash
# All new unit tests pass
make test

# Entrypoint runs a stub scenario end-to-end and writes a result file
python -m harness.main --scenario scenarios/stub.yaml --run-id test-001
cat /data/results/test-001.json   # valid RunResult JSON with status PASS or FAIL

# Cleanup runs even when a task fails (check runner logs for "cleanup" messages)
python -m harness.main --scenario scenarios/stub_failing.yaml --run-id test-002
# expect: logs show cleanup ran, result JSON status is FAIL
```

> `stub.yaml` and `stub_failing.yaml` are minimal two-task scenarios added alongside the unit tests for this phase only.

Post-phase checklist:
- [ ] `make test` output shows all new test files (`test_config`, `test_result`, `test_runner`) listed and passing
- [ ] Running the stub scenario prints each task name to stdout in order, then a cleanup line
- [ ] `cat /data/results/test-001.json` shows a JSON object with `status`, `run_id`, `tasks`, and `assertions` fields
- [ ] Running the failing stub (`test-002`) still prints cleanup lines — cleanup must run regardless of failure
- [ ] `cat /data/results/test-002.json` shows `"status": "FAIL"` but the file exists (harness did not crash)

### Dependencies

- Phase 0

---

## Phase 2 — Task Implementations

**Goal**: implement all four production task classes. Each task is independently unit-tested with mocked HTTP and Kubernetes clients.

### Deliverables

- `harness/tasks/auth.py` — `ProvisionApiKeyTask`
  - `run()`: `POST /maas-api/v1/api-keys`, appends `{id, key}` to `shared_state["api_keys"]`
  - `cleanup()`: `POST /maas-api/v1/api-keys/bulk-revoke` for all IDs in `shared_state["api_keys"]`

- `harness/tasks/inference.py` — `SendRequestsTask`
  - `run()`: concurrent OpenAI-compatible inference requests; after every completed request updates running stats in `shared_state["inference_results"]` and calls `emit_assertion_state(ctx)` (debounced: emit at most every 100ms)
  - Resolves `url` and `token` via three-level priority: explicit params → `shared_state` → `TaskContext` defaults (see ADR-012)
  - Distributes requests evenly across `key_pool` when provided
  - `cleanup()`: no-op

- `harness/tasks/metrics.py` — `CheckMaasMetricsTask`
  - `run()`: reads RHOAI/MaaS metrics endpoint; stores raw values in `shared_state["metrics"]`; always prints a formatted summary to stdout. Initial metrics: `total_requests`, `total_tokens`. Implemented as a configurable stub returning zeroes until the real endpoint is known.
  - `cleanup()`: no-op

- `harness/tasks/subscription.py` — `ApplyRateLimitSubscriptionTask`
  - `run()`: reads existing `MaaSSubscription` CR (if any) into `shared_state["original_subscription"]`; creates or patches CR with configured `rps_limit` via `kubernetes.client.CustomObjectsApi`
  - `cleanup()`: restores original CR state, or deletes the CR if it was created from scratch

- Unit tests: `test_auth.py`, `test_inference.py`, `test_metrics.py`, `test_subscription.py`

### Verification

```bash
# All task unit tests pass (mocked HTTP + mocked K8s client)
make test

# Each task class imports and instantiates without error
python -c "
from harness.tasks.auth import ProvisionApiKeyTask
from harness.tasks.inference import SendRequestsTask
from harness.tasks.metrics import CheckMaasMetricsTask
from harness.tasks.subscription import ApplyRateLimitSubscriptionTask
print('all task imports OK')
"

# Registry resolves all four task names
python -c "
from harness.tasks.registry import REGISTRY
for name in ('provision_api_key', 'send_requests', 'check_maas_metrics', 'apply_rate_limit_subscription'):
    assert name in REGISTRY, f'missing: {name}'
print('registry OK')
"
```

Post-phase checklist:
- [ ] `make test` lists all four task test files by name and all pass
- [ ] The import one-liner prints `all task imports OK` with no tracebacks
- [ ] The registry check prints `registry OK` with no `AssertionError`
- [ ] `test_inference.py` output explicitly mentions debounce and key-pool distribution test cases passing

### Dependencies

- Phase 1

---

## Phase 3 — Scenario YAMLs

**Goal**: author and validate all five scenario definitions.

### Deliverables

- `scenarios/single_key_load.yaml`
- `scenarios/multi_key_load.yaml`
- `scenarios/direct_inference.yaml`
- `scenarios/rate_limit_validation.yaml`
- `scenarios/metrics_fill.yaml`

Each YAML must:
- Declare `name`, `description`, `config` (with defaults), `tasks` (with params), `assertions`, `cleanup: automatic`
- Pass schema validation (JSON Schema or Pydantic model in `harness/config.py`)
- Resolve all `${config.<key>}` references without error when loaded by `harness/config.py`

- `harness/tests/test_scenarios.py` — parametrised test: load each scenario YAML, validate schema, resolve config, assert all task names exist in registry

### Verification

```bash
# Schema validation + registry resolution for every scenario
make test   # test_scenarios.py covers all five

# Manual spot-check: load a scenario and print its resolved config
python -c "
from harness.config import load_scenario
s = load_scenario('scenarios/single_key_load.yaml')
import json; print(json.dumps(s, indent=2))
"
# expect: all \${config.*} references resolved, no KeyError

# Confirm all five scenarios are present and named correctly
python -c "
import pathlib, yaml
for f in pathlib.Path('scenarios').glob('*.yaml'):
    s = yaml.safe_load(f.read_text())
    print(f.name, '->', s['name'])
"
```

Post-phase checklist:
- [ ] `make test` output lists `test_scenarios.py` with all five scenario names visible in the test IDs (parametrised)
- [ ] The `load_scenario` one-liner prints valid JSON with no `${...}` placeholders remaining anywhere in the output
- [ ] The scenario listing one-liner prints exactly five lines, one per YAML file
- [ ] Each printed scenario `name` matches the filename (e.g. `single_key_load.yaml` → `name: single_key_load`)

### Dependencies

- Phase 1 (config loader, registry)
- Phase 2 (task classes registered)

---

## Phase 4 — API Server

**Goal**: implement the FastAPI backend — scenario listing, run creation, run history, and SSE log streaming.

### Deliverables

- `api/db.py` — `aiosqlite` setup; schema migrations for `runs` and `task_results` tables; `init_db()` called at startup
- `api/k8s.py` — `create_job(scenario, run_id)` and `stream_pod_logs(run_id)` async generator
- `api/routes/scenarios.py` — `GET /api/scenarios`
- `api/routes/runs.py` — `POST /api/runs`, `GET /api/runs`, `GET /api/runs/{id}`
- `api/routes/logs.py` — `GET /api/runs/{id}/logs` (SSE)
- `api/main.py` — FastAPI app with all routes and `init_db()` on startup
- `api/tests/test_routes.py` — integration tests with mocked K8s client and in-memory SQLite

### Verification

```bash
# Route integration tests pass
make test

# Start the API server
uvicorn api.main:app --reload --port 8000

# Scenario list
curl -s http://localhost:8000/api/scenarios | python3 -m json.tool
# expect: JSON array with 5 entries, each with name + description

# Create a run (K8s job creation will fail locally — expect a 500 or mocked response)
curl -s -X POST http://localhost:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"scenario": "single_key_load"}' | python3 -m json.tool
# expect: JSON with run_id field

# List runs
curl -s http://localhost:8000/api/runs | python3 -m json.tool
# expect: JSON array (may be empty or contain the just-created run)

# SSE log stream (connect and wait a few seconds — Ctrl-C to stop)
curl -N http://localhost:8000/api/runs/<run-id>/logs
# expect: SSE event stream (data: ... lines)
```

Post-phase checklist:
- [ ] `make test` passes including the new `test_routes.py` file
- [ ] `curl /api/scenarios` returns a JSON array with exactly 5 objects; each has `name` and `description`
- [ ] `curl -X POST /api/runs` returns a JSON object containing a `run_id` (UUID-shaped string)
- [ ] `curl /api/runs` returns a JSON array containing the run just created
- [ ] `curl -N /api/runs/<run-id>/logs` prints `data:` lines to the terminal (SSE stream opens without error)

### Dependencies

- Phase 3 (scenarios must exist to be listed)

---

## Phase 5 — Frontend

**Goal**: implement the React + TypeScript + PatternFly UI.

### Deliverables

- `ui/` — Vite project scaffold with TypeScript, PatternFly, ESLint, Jest configured
- `ui/src/api/client.ts` — typed fetch wrappers for all backend API routes
- `ui/src/components/ScenarioList.tsx` — PatternFly list of available scenarios with a "Run" button
- `ui/src/components/RunTrigger.tsx` — modal to confirm and start a run
- `ui/src/components/LogStream.tsx` — `EventSource` consumer; scrolling `CodeBlock`; auto-reconnects
- `ui/src/components/AssertionPanel.tsx` — live assertion status; renders Passing / Failing / Pending per assertion
- `ui/src/components/RunHistory.tsx` — PatternFly `Table` of past runs
- `ui/src/App.tsx` — top-level layout
- `ui/src/tests/` — Jest + React Testing Library unit tests for each component

### Verification

```bash
# Component unit tests pass
make test

# Start both servers
make dev
# open http://localhost:5173 in a browser
```

Manual browser checks:
- [ ] Scenario list renders all 5 scenarios with a "Run" button each
- [ ] Clicking "Run" opens a confirmation modal; confirming starts the run
- [ ] Log stream panel appears and scrolls as log lines arrive via SSE
- [ ] Assertion panel shows Pending → Passing/Failing transitions in real time
- [ ] Completed run appears in the Run History table with correct status and timestamps
- [ ] Navigating away and back does not lose run history

### Dependencies

- Phase 4 (API must exist for the UI to call)

---

## Phase 6 — Containerisation & Deploy Manifests

**Goal**: package the project as a single container image and write all OpenShift manifests needed for a production deployment.

### Deliverables

**Dockerfile** (multi-stage):
1. `node-builder` stage — installs npm deps, runs `npm run build`, outputs `ui/dist/`
2. Final stage — copies `ui/dist/` + Python source; default CMD is `uvicorn api.main:app`

**Deploy manifests** (`deploy/`): `serviceaccount.yaml`, `rbac.yaml`, `pvc.yaml`, `configmap-global.yaml`, `deployment.yaml`, `service.yaml`, `route.yaml`; plus a repo-root `kustomization.yaml` (`oc apply -k .`) that also generates the `maaspal-scenarios` ConfigMap from `scenarios/*.yaml` via `configMapGenerator` — it lives at the repo root rather than inside `deploy/` because kustomize's file-load security restriction only allows a `configMapGenerator` to reference files at or below the kustomization's own directory

**Makefile** — finalised targets: `build`, `push`, `deploy`, `dev`, `test`, `lint`

### Verification

```bash
# Image builds without error
make build
podman images | grep maaspal   # image present

# Image starts as API server
docker run --rm -p 8000:8000 quay.io/rh-aiservices-bu/maaspal:latest
curl http://localhost:8000/api/scenarios   # returns scenario list from embedded ConfigMap

# Image starts as harness job entrypoint (expect a config error — no cluster available)
docker run --rm quay.io/rh-aiservices-bu/maaspal:latest \
  python -m harness.main --scenario single_key_load --run-id smoke-001
# expect: startup log lines then a config/connection error (not an import crash)

# Deploy to cluster
make push
make deploy
oc get pods -n maaspal   # API server pod Running
oc get route -n maaspal  # Route present with host

# Open Route URL in browser — UI loads and scenario list appears
```

Post-phase checklist:
- [ ] `make build` completes with no errors; `podman images | grep maaspal` shows the image
- [ ] `docker run --rm -p 8000:8000 quay.io/rh-aiservices-bu/maaspal:latest` starts and `curl http://localhost:8000/api/scenarios` returns the scenario list (not a 404 or crash)
- [ ] Running the harness entrypoint inside Docker prints startup log lines before failing on the missing cluster — no `ImportError` or `ModuleNotFoundError`
- [ ] `oc get pods -n maaspal` shows the API server pod in `Running` state (not `CrashLoopBackOff`)
- [ ] Opening the Route URL in a browser loads the MaaS:PAL UI and the scenario list is visible

### Dependencies

- Phase 5 (frontend must be built into `ui/dist/`)

---

## Phase 7 — Integration & E2E Testing

**Goal**: validate all five scenarios against a real RHOAI/MaaS environment.

### Deliverables

- `harness/tests/integration/` — integration test suite (skipped in unit test CI; requires real cluster env vars)
  - `test_single_key_load.py`, `test_multi_key_load.py`, `test_direct_inference.py`, `test_rate_limit_validation.py`, `test_metrics_fill.py`

### Verification

```bash
# Run integration tests against a live cluster (requires env vars set)
MAAS_API_URL=https://maas.<cluster-domain> \
  pytest harness/tests/integration/ -v
# expect: all 5 tests pass
```

Manual E2E checklist (browser):
- [ ] Open Route URL; UI loads
- [ ] Trigger `single_key_load` — live logs appear, assertion panel updates, run ends PASS
- [ ] Trigger `multi_key_load` — all N keys created; all N keys revoked after run
- [ ] Trigger `direct_inference` — no MaaS API keys created or revoked
- [ ] Trigger `rate_limit_validation` — `MaaSSubscription` CR restored to original state after run
- [ ] Trigger `metrics_fill` — `total_requests` and `total_tokens` non-zero in logs
- [ ] After every run: confirm no `maaspal-*` MaaS API keys remain (`GET /maas-api/v1/api-keys/search`)
- [ ] All completed runs visible in Run History with correct PASS/FAIL status

### Dependencies

- Phase 6 (deployed to a cluster)

---

## Phase 8 — CI/CD Pipeline

**Goal**: automate the full build, test, and release pipeline.

### Deliverables

- `.github/workflows/ci.yml` — lint → unit tests → Docker build; triggers on PR and push to `main`
- `.github/workflows/release.yml` — builds and pushes image on version tags (`v*`)
- Branch protection rules documented

### Verification

```bash
# Open a PR with a trivial change (e.g. update a comment)
# observe: lint, test, and build jobs all go green in GitHub Actions

# Merge to main
# observe: same 3 jobs green on the merge commit

# Create a version tag
git tag v0.1.0 && git push origin v0.1.0
# observe: release workflow runs; image pushed with both :v0.1.0 and :<git-sha> tags
docker pull quay.io/rh-aiservices-bu/maaspal:v0.1.0
```

Post-phase checklist:
- [ ] Open a PR with a trivial change; all three jobs (lint, test, build) show green ticks in the GitHub Actions tab
- [ ] A PR with a deliberate lint error (e.g. unused import) causes the lint job to fail and block merge
- [ ] Merging to `main` triggers the same three jobs and they pass on the merge commit
- [ ] Pushing `v0.1.0` tag triggers the release workflow; `docker pull quay.io/rh-aiservices-bu/maaspal:v0.1.0` succeeds after it completes

### Dependencies

- Phase 7 (all tests must exist before CI can run them)

---

## Phase 9 — Documentation

**Goal**: produce complete, reviewed documentation for operators, developers, and scenario authors.

### Deliverables

- `README.md` — project overview, architecture diagram, quickstart, links to detailed docs
- `docs/guides/quickstart.md` — step-by-step: prerequisites, `make deploy`, verify the Route, trigger a scenario
- `docs/guides/scenario-authoring.md` — format reference, available tasks, assertion syntax, config interpolation
- `docs/guides/task-development.md` — implementing the `Task` ABC, registering in `registry.py`, writing unit tests
- `docs/guides/troubleshooting.md` — common failure modes and remediation steps
- `docs/reference/api.md` — human-readable summary of each API endpoint
- `docs/reference/scenario-schema.md` — full YAML schema reference

### Verification

Post-phase checklist (follow each guide cold, with no other context):
- [ ] `README.md` — can describe what MaaS:PAL does and how to deploy it after reading only the README
- [ ] `docs/guides/quickstart.md` — deploy to a fresh cluster and trigger the first scenario run using only the quickstart steps; no improvisation needed
- [ ] `docs/guides/scenario-authoring.md` — write a new scenario YAML from scratch; it loads without validation errors and runs via the CLI
- [ ] `docs/guides/task-development.md` — add a new stub task class; it appears in `REGISTRY` and a scenario can invoke it; unit test passes
- [ ] `docs/guides/troubleshooting.md` — simulate a MaaS API auth failure and locate the fix using only the troubleshooting guide

### Dependencies

- Phase 7 (docs must reflect final, tested behaviour)

---

## Dependency Graph

```
Phase 0 (Foundation)
    └── Phase 1 (Harness Core)
            └── Phase 2 (Task Implementations)
                    └── Phase 3 (Scenario YAMLs)
                            └── Phase 4 (API Server)
                                    └── Phase 5 (Frontend)
                                            └── Phase 6 (Containerisation)
                                                    └── Phase 7 (E2E Testing)
                                                            ├── Phase 8 (CI/CD)
                                                            └── Phase 9 (Documentation)
```

---

## Notes

- **`check_maas_metrics` stub**: the metrics endpoint is TBD. Phase 2 implements the task as a configurable stub returning zeroes. The real implementation is slotted for Phase 7 when a live cluster is available.
- **Frontend build in Dockerfile**: the multi-stage Dockerfile in Phase 6 means the frontend must reach a buildable state (Phase 5) before the final image can be produced.
- **Integration tests** (Phase 7) require cluster access and are excluded from the standard CI unit-test job. They run in a separate manual-trigger workflow.
- **PVC storage class**: `ReadWriteMany` is assumed. If the target cluster only supports `ReadWriteOnce`, the API server and Job pods must be colocated on the same node. Validate in Phase 6.
