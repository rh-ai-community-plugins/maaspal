# Changelog

## Unreleased

- The **Test runs** page is split into **Scenarios** (a catalog with search and
  category / custom-or-built-in / type filters) and **Runs** (run history, full
  width). `/maaspal` now opens Scenarios.
- Scenarios that aren't shipped with MaaS:PAL are labelled **Custom** and listed
  first: under their own `category:` if it's a built-in one, otherwise in a
  Custom category at the top.
- **MaaS setup** is renamed **MaaS overview** (`/maaspal/overview`; the old
  `/maaspal/setup` path redirects), since it only shows the configuration.
- The header reads **MaaS:PAL**, with a mascot logo that changes on every page,
  drawn from a light-theme or dark-theme set.

## 0.1.0

- MaaS:PAL becomes an RHOAI Dashboard community plugin (ADR-026): a Module
  Federation frontend under **Community plugins → MaaS:PAL** (Test runs, MaaS
  setup), PatternFly 6, the FastAPI backend as the plugin's BFF, a Helm chart
  (`maaspal-chart`) and `plugin.yaml`.
- Every API call is checked against the dashboard user's token (the
  `maaspal-user` Role); a NetworkPolicy limits the BFF to the dashboard.
- The standalone Route, the Vite build and the kustomize manifests are removed.
