# Deploying MaaS:PAL on OpenShift

MaaS:PAL deploys as two services (the frontend remote and the FastAPI BFF) plus
the RBAC its harness needs, then registers with the RHOAI dashboard.

## Prerequisites

- RHOAI with Models as a Service (MaaS) enabled, and the RHOAI dashboard running
  (`rhods-dashboard` in `redhat-ods-applications`; on Open Data Hub use
  `odh-dashboard` in `opendatahub` throughout)
- `oc` logged in with cluster-admin: the chart creates ClusterRoles, and
  registration edits the dashboard Deployment
- Helm 3.8+

## 1. Install the plugin

```bash
helm install maaspal oci://quay.io/rh-ai-community-plugins/maaspal-chart \
  --version 0.1.0 \
  --namespace cp-maaspal \
  --create-namespace

oc label namespace cp-maaspal maas.opendatahub.io/gateway-access=true --overwrite
```

From a checkout, use `chart/` instead of the OCI URL, or `make deploy`
(which also applies the label, and takes `REGISTRY`/`IMAGE_TAG` to install
your own images).

This creates:

| Object | Name | Purpose |
|---|---|---|
| Deployment + Service | `maaspal` (port 8080) | nginx serving `remoteEntry.js` and the UI chunks |
| Deployment + Service | `maaspal-bff` (port 3000) | FastAPI BFF; launches runs as Jobs |
| PersistentVolumeClaim | `maaspal-data` | run history (SQLite), logs, live run files. Kept on uninstall. |
| ConfigMap | `maaspal-global-config` | cluster settings for the BFF and every harness Job |
| ServiceAccount | `maaspal` | the identity the BFF and the harness act as |
| Roles / ClusterRoles | see [Permissions](#permissions) | what the harness may do |
| Role | `maaspal-user` | who may use MaaS:PAL (see [Access](#3-give-people-access)) |
| NetworkPolicy | `maaspal-bff` | only the dashboard namespace may reach the BFF |

The namespace label lets the MaaS gateway accept HTTPRoutes for the throwaway
models that `denied_without_auth_policy`, `gateway_overhead` and
`multi_model_load` deploy in the plugin namespace.

### MaaS settings

At install time the chart reads the cluster's apps domain
(`ingresses.config.openshift.io/cluster`) and sets:

- `MAAS_API_URL` = `https://maas.<apps domain>`
- `MAAS_METRICS_URL` = `https://thanos-querier-openshift-monitoring.<apps domain>/api/v1/query`

Override either if your install differs:

```bash
helm upgrade --install maaspal chart/ -n cp-maaspal \
  --set maas.apiUrl=https://maas.apps.example.com \
  --set maas.metricsUrl=https://thanos-querier-openshift-monitoring.apps.example.com/api/v1/query \
  --set maas.defaultModel=my-model
```

`helm install --dry-run` and `helm template` can't read the cluster, so they
render these URLs empty.

## 2. Register with the RHOAI dashboard

The dashboard loads plugins from its `MODULE_FEDERATION_CONFIG`. MaaS:PAL needs
both a `backend` entry (the frontend remote) and a `proxyService` entry (the
BFF, with the user's token forwarded):

```bash
oc get configmap federation-config \
  -n redhat-ods-applications \
  -o jsonpath='{.data.module-federation-config\.json}' \
| python3 -c "
import json, sys
config = json.load(sys.stdin)
config = [e for e in config if e.get('name') != 'maaspal']
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

`authorize: True` on the `proxyService` is required: the BFF rejects requests
without the user's token (401).

### Why `MODULE_FEDERATION_CONFIG` instead of the ConfigMap?

The RHOAI operator reconciles the `federation-config` ConfigMap, so direct edits
to it may be reverted. The environment variable on the Deployment overrides the
ConfigMap and survives reconciliation.

New dashboard pods roll out automatically. After about two minutes, reload the
dashboard.

## 3. Give people access

The BFF acts with the `maaspal` ServiceAccount's rights, so every API call is
checked against the dashboard user: a SelfSubjectAccessReview, made with the
user's own token, for verb `use` on `harness.maaspal.rh-ai-community-plugins.io`
in the plugin namespace. The chart's `maaspal-user` Role grants exactly that and
nothing else. Cluster-admins pass through their wildcard rules.

```bash
oc create rolebinding maaspal-users -n cp-maaspal --role=maaspal-user --group=<group>
```

or, through Helm:

```bash
helm upgrade maaspal chart/ -n cp-maaspal --reuse-values \
  --set access.groups[0]=<group> --set access.users[0]=<user>
```

A user without it sees "You don't have access to MaaS:PAL" on the plugin's
pages. Decisions are cached for 60 seconds per token.

## 4. Verify

```bash
oc get pods -n cp-maaspal          # maaspal and maaspal-bff Running
```

Confirm the registration:

```bash
oc set env deployment/rhods-dashboard -n redhat-ods-applications --list \
  | grep '^MODULE_FEDERATION_CONFIG=' \
  | python3 -c "
import json, sys
data = json.loads(sys.stdin.read().split('=', 1)[1])
for entry in data:
    print(entry['name'] + (' (+ BFF proxy)' if entry.get('proxyService') else ''))
"
```

Then open the dashboard: **Community plugins → MaaS:PAL → Test runs** lists
the scenarios. Launch `smoke_test` as a first check.

## Permissions

Each grant the harness ServiceAccount holds is a chart value:

| Value | Default | Grants |
|---|---|---|
| (always) | on | Jobs (incl. `patch`, for Stop), pods and pod logs in the plugin namespace |
| `rbac.maasReadonly` | `true` | Cluster-wide read of MaaS, Kuadrant, Gateway API, KServe and RHOAI config objects; `get` on individual Secrets (labels only, never contents) |
| `rbac.subscriptionWrite` | `true` | Create/patch/delete `MaaSSubscription`s and `MaaSAuthPolicy`s |
| `rbac.modelWrite` | `true` | Create/delete `LLMInferenceService`s, `MaaSModelRef`s and Routes |
| `rbac.monitoring` | `true` | `cluster-monitoring-view` (Thanos) |
| `rbac.userProvisioning` | `false` | Create ServiceAccounts and mint their tokens in the plugin namespace. Only `rate_limit_per_user_or_shared` needs it; review before enabling. |

ClusterRoles and ClusterRoleBindings are named `<namespace>-maaspal-…`, so two
installs in different namespaces don't collide.

## Upgrading

```bash
helm upgrade maaspal oci://quay.io/rh-ai-community-plugins/maaspal-chart --version <new> -n cp-maaspal --reuse-values
```

The BFF restarts with `Recreate` (one replica, ReadWriteOnce volume). Runs in
progress keep going: they are Jobs, and their results are picked up when the
new BFF starts.

## Uninstalling

Remove the dashboard entry:

```bash
oc get configmap federation-config \
  -n redhat-ods-applications \
  -o jsonpath='{.data.module-federation-config\.json}' \
| python3 -c "
import json, sys
config = json.load(sys.stdin)
print(json.dumps([e for e in config if e.get('name') != 'maaspal']))
" > /tmp/mf-config-reduced.json

oc set env deployment/rhods-dashboard \
  -n redhat-ods-applications \
  "MODULE_FEDERATION_CONFIG=$(cat /tmp/mf-config-reduced.json)"
```

Then the release:

```bash
helm uninstall maaspal -n cp-maaspal
oc delete pvc maaspal-data -n cp-maaspal   # optional: drops run history
oc delete namespace cp-maaspal             # optional
```

Uninstalling doesn't touch resources a run left in place with auto cleanup
off. Clean those up from the run's page ("Clean Up Now") before uninstalling.

## Helm chart reference

| Parameter | Default | Description |
|---|---|---|
| `namespace` | `cp-maaspal` | Namespace for all namespaced resources (and the harness's Jobs and throwaway models) |
| `maas.apiUrl` | `""` (derived) | MaaS base URL |
| `maas.metricsUrl` | `""` (derived) | Thanos Querier instant-query URL |
| `maas.defaultModel` | `""` | Model used when a scenario doesn't name one |
| `maas.defaultSubscription` | `""` | Empty = MaaS auto-selects the highest-priority subscription |
| `maas.stopGracePeriodSeconds` | `120` | Time a stopped run gets to clean up before it's killed |
| `image.repository` / `image.tag` | `quay.io/rh-ai-community-plugins/maaspal` / appVersion | Frontend image |
| `bff.image.repository` / `bff.image.tag` | `quay.io/rh-ai-community-plugins/maaspal-bff` / appVersion | BFF and harness image |
| `bff.persistence.size` / `storageClassName` | `2Gi` / default | Run data volume |
| `bff.resources` | 100m/256Mi requests | BFF resources |
| `serviceAccount.name` | `""` (= `maaspal`) | Harness ServiceAccount name |
| `access.users` / `access.groups` | `[]` | Bound to the `maaspal-user` Role |
| `rbac.*` | see [Permissions](#permissions) | Optional harness grants |
| `networkPolicy.enabled` | `true` | Restrict BFF ingress to the dashboard namespace |
| `networkPolicy.dashboardNamespace` | `redhat-ods-applications` | `opendatahub` on ODH |
