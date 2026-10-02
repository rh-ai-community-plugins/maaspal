# Changelog

## 0.1.0

- MaaS:PAL becomes an RHOAI Dashboard community plugin (ADR-026): a Module
  Federation frontend under **Community plugins → MaaS:PAL** (Test runs, MaaS
  setup), PatternFly 6, the FastAPI backend as the plugin's BFF, a Helm chart
  (`maaspal-chart`) and `plugin.yaml`.
- Every API call is checked against the dashboard user's token (the
  `maaspal-user` Role); a NetworkPolicy limits the BFF to the dashboard.
- The standalone Route, the Vite build and the kustomize manifests are removed.
