# MaaS:PAL

The testing harness that fights for you (to destruction).

MaaS:PAL is a community plugin for the **Red Hat OpenShift AI (RHOAI) Dashboard**
that tests a **Models as a Service (MaaS)** setup on a live cluster. It answers
questions like "is my subscription's rate limit actually enforced?" or "does my
subscription reach every model it covers?". It acts through MaaS's own
user-facing APIs, the way a real user would.

In the dashboard, open **Community plugins → MaaS:PAL**. Pick a scenario from
the **Scenarios** catalog (search, or filter by category, custom/built-in and
type), adjust its settings and launch it. **Runs** lists every run. The run executes in the
cluster as a Kubernetes Job, and its page shows what happened step by step:
live progress, traffic charts, the checks, a plain-language verdict, and what
the run created and cleaned up. **MaaS overview** shows the cluster's MaaS
configuration (models, subscriptions, auth policies, rate limiting, networking).

## What's Inside

| Part | What it is |
|---|---|
| Frontend (`src/`) | React + PatternFly 6, loaded by the dashboard at runtime through Webpack Module Federation (`remoteEntry.js`, served by nginx) |
| BFF (`bff/`) | FastAPI backend behind the dashboard's `proxyService` (`/maaspal/api` → `/api`). Launches and stops runs, captures their logs, keeps run history (SQLite on a PVC) and reads the MaaS setup. Every request is checked against the dashboard user's own token. |
| Harness (`bff/harness/`) | The test runner. Each run is a Kubernetes Job using the BFF image. Scenarios are YAML files in `bff/scenarios/`. |
| Helm chart (`chart/`) | Deploys the frontend, the BFF, the PVC and the RBAC the harness needs |

## Scenarios

| Category | Scenario | Question it answers |
|---|---|---|
| Quick check | `smoke_test` | Is MaaS working end to end? |
| Quick check | `verify_subscription` | Does my subscription work for every model it covers? |
| Quick check | `request_types` | Which request types does my model support (and if one fails, was it MaaS or the model)? |
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
you turn auto cleanup off.

Every scenario that sends inference requests has two advanced settings:
**Request API** (`/v1/chat/completions` by default, or `/v1/completions`,
`/v1/responses`, `/v1/embeddings`) and **Streaming** (off by default).

**Your own scenarios:** a new YAML file in `bff/scenarios/` shows up in the
Scenarios catalog with a **Custom** label, ahead of the built-in ones. Give it
one of the categories above (`category: "Rate limits"`) to list it there, or
leave `category:` out and it goes in a Custom category at the top. Scenarios are
baked into the BFF image, so rebuild it.

## Quick Start

**Prerequisites:** Helm, `oc` logged in with cluster-admin (the chart creates
ClusterRoles, and registering the plugin edits the dashboard Deployment), and
RHOAI with MaaS enabled.

### 1. Install

```bash
helm install maaspal oci://quay.io/rh-ai-community-plugins/maaspal-chart \
  --version 0.1.0 \
  --namespace cp-maaspal \
  --create-namespace

oc label namespace cp-maaspal maas.opendatahub.io/gateway-access=true --overwrite
```

Or from a checkout: `helm install maaspal chart/ -n cp-maaspal --create-namespace`
(or `make deploy`).

The MaaS URL (`https://maas.<apps domain>`) and the Thanos Querier URL are read
from the cluster at install time; override them with `--set maas.apiUrl=…` and
`--set maas.metricsUrl=…`. The namespace label lets the MaaS gateway accept the
throwaway models some scenarios deploy there.

### 2. Register with the RHOAI Dashboard

```bash
oc get configmap federation-config \
  -n redhat-ods-applications \
  -o jsonpath='{.data.module-federation-config\.json}' \
| python3 -c "
import json, sys
config = json.load(sys.stdin)
config.append({
  'name': 'maaspal',
  'backend': {
    'remoteEntry': '/remoteEntry.js',
    'authorize': False,
    'tls': False,
    'service': {'name': 'maaspal', 'namespace': 'cp-maaspal', 'port': 8080}
  },
  'proxyService': [{
    'path': '/maaspal/api',
    'pathRewrite': '/api',
    'authorize': True,
    'tls': False,
    'service': {'name': 'maaspal-bff', 'namespace': 'cp-maaspal', 'port': 3000}
  }]
})
print(json.dumps(config))
" > /tmp/mf-config-extended.json

oc set env deployment/rhods-dashboard \
  -n redhat-ods-applications \
  "MODULE_FEDERATION_CONFIG=$(cat /tmp/mf-config-extended.json)"
```

After the dashboard pods roll out (about two minutes), reload the dashboard:
**Community plugins → MaaS:PAL** appears in the sidebar.

### 3. Give people access

Cluster-admins can use MaaS:PAL straight away. Anyone else needs the chart's
`maaspal-user` Role in the plugin namespace:

```bash
oc create rolebinding maaspal-users -n cp-maaspal --role=maaspal-user --group=<group>
```

or install with `--set access.groups[0]=<group>`. Without it, the plugin's pages
show "You don't have access to MaaS:PAL".

See [Deploying on OpenShift](docs/deployment/OPENSHIFT_DEPLOY.md) for the full
guide: chart values, permissions, upgrading and uninstalling.

## Permissions

The harness acts with its own ServiceAccount (`maaspal`), which needs
cluster-wide grants. Each is a chart value, so you can leave one out; scenarios
that need it then fail with a permission error.

| Value | Grants | Needed by |
|---|---|---|
| (always) | Jobs, pods and logs in the plugin namespace (incl. `patch` on Jobs, for Stop) | everything |
| `rbac.maasReadonly` | Cluster-wide read of MaaS, Kuadrant, Gateway API and KServe resources, plus `get` on individual Secrets (to check an external provider's credential Secret is labelled correctly; never their contents) | MaaS overview pages, model health, reading subscription limits |
| `rbac.subscriptionWrite` | Create/patch/delete MaaS subscriptions and auth policies | scenarios that create temporary subscriptions or auth policies |
| `rbac.modelWrite` | Create/delete LLMInferenceServices, MaaSModelRefs and Routes | scenarios that deploy throwaway models |
| `rbac.monitoring` | `cluster-monitoring-view` (Thanos) | MaaS metrics checks |
| `rbac.userProvisioning` (off by default) | Create ServiceAccounts and mint their tokens in the plugin namespace | `rate_limit_per_user_or_shared`. **The most sensitive grant here.** |

Because the backend holds these rights, every API call checks the dashboard
user's token (a SelfSubjectAccessReview for the `maaspal-user` Role's virtual
permission), and a NetworkPolicy only lets the dashboard reach the backend. See
[ADR-026](docs/architecture/adrs/ADR-026-rhoai-community-plugin.md).

## Develop

Developing a dashboard plugin is easiest with a running RHOAI dashboard
connected to a real cluster (see the seed project's
[local setup guide](https://github.com/rh-ai-community-plugins/hello-world/blob/main/docs/development/LOCAL_SETUP.md)).
For work on the plugin's own pages, the standalone mode is enough:

```bash
make install           # npm ci + pip install -e "bff[dev]" (use a virtualenv)
make dev-bff           # FastAPI on :3000, access check off
make dev-standalone    # webpack dev server on :9500 — open http://localhost:9500/maaspal
make dev               # same, but proxying the rest of /maaspal to a dashboard on :8443
```

Running real scenarios locally needs the cluster env vars the chart's
ConfigMap provides (`MAAS_API_URL`, …) and `oc` access.

```bash
make validate          # tsc + eslint + jest, ruff + mypy + pytest, helm lint
make build             # dist/remoteEntry.js
make image-build       # both container images
```

`CLAUDE.md` is the detailed reference: architecture, the scenario YAML format,
every task, how assertions work, and how to verify changes.
`docs/architecture/adrs/` records the design decisions, and
`docs/architecture/empirical-verification-checklist.md` tracks which claims
have been confirmed against a live cluster.

## License

Apache-2.0
