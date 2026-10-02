# MaaS:PAL
The testing harness that fights for you (to destruction)

MaaS:PAL tests a Red Hat OpenShift AI **Models as a Service (MaaS)** setup
against a live cluster. It answers questions like "is my subscription's rate
limit actually enforced?" or "does my subscription reach every model it
covers?". It acts through MaaS's own user-facing APIs, the way a real user
would.

You pick a scenario in the web UI, adjust its settings and launch it. The run
executes in the cluster as a Kubernetes Job, and its page shows what happened
step by step: live progress, traffic charts, the checks, a plain-language
verdict, and what the run created and cleaned up.

## Scenarios

| Category | Scenario | Question it answers |
|---|---|---|
| Quick check | `smoke_test` | Is MaaS working end to end? |
| Quick check | `verify_subscription` | Does my subscription work for every model it covers? |
| Rate limits | `verify_subscription_rate_limit` | Is my subscription's rate limit enforced? |
| Rate limits | `keys_share_user_budget` | Do all my keys share one budget? |
| Rate limits | `rate_limit_window_recovery` | Does access come back after the rate-limit window? |
| Rate limits | `subscription_auto_selection` | Which subscription do my keys get? |
| Rate limits | `rate_limit_per_user_or_shared` | Are limits per user or shared? |
| Access control | `denied_without_auth_policy` | Is access denied without an auth policy? |
| API keys | `api_key_lifecycle` | Do API keys behave correctly? |
| Usage metrics | `usage_metrics_accuracy` | Do MaaS usage metrics match real traffic? |
| Performance | `load_test` | How does MaaS hold up under load? |
| Performance | `gateway_overhead` | How much latency does the MaaS gateway add? |
| Performance | `multi_model_load` | Does MaaS hold up with many models and subscriptions? |
| Diagnostics | `model_config_health` | Are my models wired up correctly? |

"Verify" scenarios use your existing setup and only create API keys.
"Explore" scenarios create temporary models, subscriptions or identities to
probe how MaaS behaves. Everything a run creates is removed afterwards, unless
you turn auto cleanup off. Scenarios are YAML files in `scenarios/`; drop in a
new one and it appears in the UI under "Custom".

## Deploy

Requires `oc` logged in to the cluster with permission to create the
`maaspal` namespace and the RBAC below.

1. Set your cluster's URLs in `deploy/configmap-global.yaml`: `MAAS_API_URL`,
   and `MAAS_METRICS_URL` (Thanos Querier, for the MaaS metrics checks).
2. Build and push the image (UI and backend in one image):
   `make push IMAGE=<registry>/maaspal:<tag>`
3. Deploy: `make deploy`. This runs `oc apply -k .` and restarts the API server.
4. Open the `maaspal` Route in the `maaspal` namespace.

### Permissions

`make deploy` applies **every** RBAC file below (they're all listed in
`kustomization.yaml`). Review them before deploying on a shared cluster. To
leave a grant out, remove its line from `kustomization.yaml`; the scenarios
that need it will then fail with a permission error.

| File | Grants | Needed by |
|---|---|---|
| `rbac.yaml` | Jobs, pods and logs in `maaspal` (incl. `patch` on Jobs, for Stop) | everything |
| `rbac-maas-readonly.yaml` | Cluster-wide read of MaaS, Kuadrant, Gateway API and KServe resources, plus `get` on individual Secrets (to check an external provider's credential Secret is labelled correctly; never their contents) | MaaS Setup tab, model health, reading subscription limits |
| `rbac-maas-subscription-write.yaml` | Create/patch/delete MaaS subscriptions and auth policies | scenarios that create temporary subscriptions or auth policies |
| `rbac-model-write.yaml` | Create/delete LLMInferenceServices, MaaSModelRefs and Routes | scenarios that deploy throwaway models |
| `rbac-monitoring.yaml` | `cluster-monitoring-view` (Thanos) | MaaS metrics checks |
| `rbac-user-provisioning.yaml` | Create ServiceAccounts and mint their tokens in `maaspal` | multi-user scenarios. **The most sensitive grant here.** |

`make deploy` also labels the `maaspal` namespace
`maas.opendatahub.io/gateway-access=true`.

## Develop

```bash
make dev    # FastAPI + Vite dev servers
make test   # pytest (harness + API) and Jest (UI)
make lint   # ruff, mypy, eslint
```

`CLAUDE.md` is the detailed reference: architecture, the scenario YAML format,
every task, how assertions work, and how to verify changes.
`docs/architecture/adrs/` records the design decisions, and
`docs/architecture/empirical-verification-checklist.md` tracks which claims
have been confirmed against a live cluster.
