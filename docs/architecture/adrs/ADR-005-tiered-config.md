# ADR-005: Two-level Tiered Configuration (Global ConfigMap + Scenario YAML)

## Status

Accepted — amended (see the Update below)

## Context

MaaS:PAL needs configuration at two distinct scopes:

1. **Cluster-level**: the MaaS API base URL, default model, and default subscription. These are the same for every scenario run on a given cluster and should not need to be repeated in every scenario file.
2. **Scenario-level**: run parameters specific to a scenario (request count, concurrency, rate limit, target URL, etc.). These vary per scenario and should be tunable without touching cluster-wide settings.

Options considered:

- **Single flat ConfigMap**: all settings in one place, merged at deploy time. Makes per-scenario tuning awkward.
- **Environment variables only**: no YAML scenario config; everything is an env var. Inflexible; can't version scenario configs alongside code.
- **Two-level merge**: a global ConfigMap provides cluster-level defaults as env vars; each scenario YAML has a `config:` section for scenario-level params. Scenario YAML values take precedence where keys overlap.

## Decision

Use a **two-level tiered configuration**:

1. **Global ConfigMap** (`configmap-global.yaml`) — injected as environment variables into every Job pod. Contains only cluster-level settings:
   ```yaml
   MAAS_API_URL: "https://maas.apps.mycluster.example.com"
   DEFAULT_MODEL: "granite-3-8b-instruct"
   DEFAULT_SUBSCRIPTION: ""   # empty = auto-select highest priority
   ```

2. **Scenario YAML `config:` section** — per-scenario parameters with sensible defaults. Resolved at runtime by the harness config loader. Scenario values override globals where keys overlap.

Scenario YAML params are referenced in task `params` using `${config.<key>}` interpolation.

## Consequences

**Positive:**
- Cluster operators set the global ConfigMap once; scenario authors never need to know the cluster domain or model name.
- Scenario configs ship with defaults so a run works out of the box without requiring the user to set any values before triggering.
- Scenario YAML files are self-documenting: reading the `config:` section tells you exactly what parameters the scenario accepts and what the defaults are.
- No hard ceilings — users can override any scenario config value to any number they want.

**Negative:**
- Two places to look when debugging a config problem: the ConfigMap and the scenario YAML.
- The interpolation syntax (`${config.<key>}`) must be implemented and tested in the harness config loader.

**Neutral:**
- The global ConfigMap intentionally contains no scenario-specific values (request counts, rate limits, etc.). Those live exclusively in the scenario YAML `config:` section.

## Update: a third level

A third, highest-precedence level was added: `MAASPAL_CONFIG_OVERRIDES`, a JSON dict the API server injects into the Job. It carries the values the user edited in the UI's launch form before starting the run, and is applied over both the global ConfigMap and the scenario YAML (`harness/config.py:load_scenario`). See CLAUDE.md, "Tiered Config".
