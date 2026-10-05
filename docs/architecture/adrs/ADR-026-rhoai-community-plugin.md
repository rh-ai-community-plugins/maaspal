# ADR-026: MaaS:PAL as an RHOAI Dashboard Community Plugin

## Status

Accepted. Supersedes ADR-011's build setup (Vite, PatternFly 5) and the standalone Route; amends ADR-001 (the BFF image is still the harness image, but the UI now has its own image).

## Context

MaaS:PAL ran as a standalone app: a Vite + React + PatternFly 5 UI served by FastAPI behind its own OpenShift Route, deployed with kustomize (`oc apply -k .`). Its users are RHOAI admins, who already work in the RHOAI dashboard. The dashboard loads **community plugins** at runtime through Webpack Module Federation, and `rh-ai-community-plugins/hello-world` is the seed project that defines what a plugin ships: a federated frontend served by nginx, an optional BFF behind the dashboard's `proxyService`, a Helm chart published as an OCI artifact, a `plugin.yaml` manifest, and CI.

Two things in MaaS:PAL don't map directly onto the seed:

- **The seed's BFF is Node/Express.** MaaS:PAL's backend is FastAPI, and the same image runs every harness Job.
- **The seed's BFF uses the user's token for every cluster call.** MaaS:PAL's backend acts as its own ServiceAccount, which holds far more than a typical dashboard user: it creates Jobs, MaaSSubscriptions, MaaSAuthPolicies and throwaway models, and can optionally mint ServiceAccount tokens (ADR-023). Through the dashboard's proxy, any dashboard user could reach it.

## Decision

### Plugin only

The UI becomes a Module Federation remote (`maaspal`, route prefix `/maaspal`), loaded under the shared **Community plugins** sidebar section with two entries, **Test runs** (`/maaspal/runs`) and **MaaS setup** (`/maaspal/setup`) — later three: **Scenarios** (`/maaspal/scenarios`), **Runs** (`/maaspal/runs`) and **MaaS setup**. The standalone Route, the Vite build and FastAPI's static file mount are removed: one UI, one way to reach it.

- Webpack 5 with the seed's config. React, react-dom, react-router-dom, `@patternfly/react-core` and `@openshift/dynamic-plugin-sdk` are shared singletons from the host. PatternFly 5 → 6 (the host's version).
- Hash routing (`#run/<id>`, `#maas`) becomes react-router routes under the host's router. The custom masthead and `<Page>` are gone: the dashboard owns the page chrome and the scroll container. The plugin shows the required **Community Plugin** banner.
- Monaco stays self-hosted and YAML-only (no CDN), with the worker bundled through webpack 5's `new Worker(new URL(...))`. With `publicPath: 'auto'` it is served from the plugin's own path, like every other chunk, and still loads only when a YAML view opens.
- Theme CSS is scoped to the plugin's root or to `maaspal-*` classes so nothing leaks into the host.

### FastAPI stays as the BFF

The dashboard's `proxyService` is a plain HTTP reverse proxy to a Kubernetes Service (`name`/`namespace`/`port`); `authorize: true` just adds `Authorization: Bearer <user token>`. Nothing in the plugin contract depends on the BFF's language, so the FastAPI app is registered as the BFF (`/maaspal/api` → `/api`, port 3000) instead of being rewritten. The Python project moves to `bff/` to match the seed's layout; package names (`api`, `harness`) are unchanged. Scenarios are baked into the BFF image instead of mounted from a kustomize-generated ConfigMap.

### Every API call is gated on the dashboard user

`api/auth.py` is a FastAPI dependency on the whole app (except `/api/health`, for the kubelet). It requires the forwarded bearer token (401 without one) and makes a **SelfSubjectAccessReview with that token** for a virtual permission: verb `use` on `harness.maaspal.rh-ai-community-plugins.io` in the plugin's namespace (403 if denied, 503 if the API server can't be reached). Decisions are cached for 60 s per token hash, since the UI polls every 1–2 s.

- A virtual resource, not a real one (such as `create jobs`): granting it gives a user no cluster power of its own. The chart's `maaspal-user` Role grants exactly this permission; admins bind it (`access.users`/`access.groups`). Cluster-admins pass through their wildcard rules.
- A *self* review needs no RBAC on the maaspal ServiceAccount (no `create subjectaccessreviews`), and an invalid token can't review itself, so it's denied too.
- Reads are gated as well as writes. The MaaS setup pages expose the cluster's MaaS configuration, and `get` on Secrets backs one of them.
- A NetworkPolicy lets only the dashboard namespace reach the BFF, so other pods can't bypass the dashboard and its token forwarding.
- `MAASPAL_AUTH_MODE=off` disables the check for local development only.

### Deployment through Helm

`chart/` (`maaspal-chart`, `nameOverride: maaspal`, default namespace `cp-maaspal`) replaces `deploy/` and `kustomization.yaml`. Each former RBAC file becomes a template behind an `rbac.*` value. Cluster-scoped names are prefixed with the namespace so installs don't collide. `rbac.userProvisioning` is off by default (ADR-023's grant is the most sensitive). The MaaS and Thanos URLs are derived from the cluster's apps domain with Helm `lookup` when not set. The ServiceAccount, ConfigMap and PVC names reach the BFF through the global ConfigMap (`MAASPAL_SERVICE_ACCOUNT`, `MAASPAL_GLOBAL_CONFIGMAP`, `MAASPAL_DATA_PVC`, `NAMESPACE`), so Jobs work under any release name. Scenarios that deploy throwaway models in the harness's own namespace default to `${config.NAMESPACE}` instead of a hardcoded `maaspal`.

## Consequences

- MaaS:PAL appears where RHOAI admins already work, and installs the way other community plugins do (Helm OCI chart + `MODULE_FEDERATION_CONFIG`).
- Two images instead of one: `maaspal` (nginx + static files) and `maaspal-bff` (FastAPI + harness).
- Every user needs the `maaspal-user` binding. Before, anyone who could open the Route could launch runs as the ServiceAccount.
- Registering the plugin needs cluster-admin (it edits the dashboard Deployment), and the dashboard must be restarted to pick it up.
- The UI can no longer be opened without a dashboard, except through the webpack dev server (`make dev-standalone`).
- Shared singletons must stay version-compatible with the dashboard (React 18, PatternFly 6, react-router 7).
