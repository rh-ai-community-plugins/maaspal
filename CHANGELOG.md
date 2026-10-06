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
- Every page works in the dashboard's dark theme: all colours come from one
  set of light/dark tokens (`src/app/styles/tokens.css`), statuses from one
  map (`src/app/status.ts`), and a test rejects hard-coded colours. See
  `docs/design/colors-and-components.md`.
- The header reads **MaaS:PAL**, with a mascot logo that changes on every page,
  drawn from a light-theme or dark-theme set.
- **Send from user browser** (ADR-027): an advanced setting on every scenario
  that sends inference requests. The run page sends the requests from the
  user's browser straight to the MaaS gateway; the harness still runs
  everything else and tallies what the browser reports, so checks, charts and
  logs work as before. On by default for the small quick checks, off for rate
  limits and load. A gateway that refuses cross-origin calls fails the step
  with a "Blocked by the browser" finding — reported as the result.
- Error messages in run logs and results are exactly what came back: the
  status line and the whole response body (or the client's own error), no
  longer cut to 80–200 characters.
- "What happened" labels each step that sent requests with where it sent from
  (pod or browser) and which endpoint it used, streamed or not.
- **Which request types does my model support?** no longer has a check: the
  Request types table and finding are the answer, and a type that fails never
  fails the run.

## 0.1.0

- MaaS:PAL becomes an RHOAI Dashboard community plugin (ADR-026): a Module
  Federation frontend under **Community plugins → MaaS:PAL** (Test runs, MaaS
  setup), PatternFly 6, the FastAPI backend as the plugin's BFF, a Helm chart
  (`maaspal-chart`) and `plugin.yaml`.
- Every API call is checked against the dashboard user's token (the
  `maaspal-user` Role); a NetworkPolicy limits the BFF to the dashboard.
- The standalone Route, the Vite build and the kustomize manifests are removed.
