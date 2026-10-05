# ADR-020: Scenario Categories in the UI

## Status

Accepted

## Context

`GET /api/scenarios` and `ui/src/components/ScenarioList.tsx` rendered every scenario as one flat list, with no concept of grouping anywhere in the codebase — not in the YAML schema, not in the API response, not in the `Scenario` TypeScript type. With 7 scenarios now spanning distinctly different concerns (load testing, rate-limit enforcement, access control, metrics validation, API key lifecycle), a flat list no longer communicates what each one is actually for at a glance. There was also no designated place for someone to know where a scenario they write themselves will show up in the UI.

## Decision

**Scenario YAML gains an optional top-level `category:` field.** A scenario with no `category:` defaults to `"Custom"` (`api/routes/scenarios.py:list_scenarios`) — this is the only mechanism needed to make a permanent "Custom" bucket work for anything dropped into `scenarios/` later, without a separate placeholder or registration step. The 7 built-in scenarios are assigned across 5 categories: Load Testing (`single_key_load`, `multi_key_load`, `direct_inference`), Rate Limiting (`rate_limit_validation`), Access Control (`access_denied_no_policy`), Metrics Validation (`metrics_fill`), API Key Lifecycle (`api_key_lifecycle`, ADR-019).

**`Scenario` (`ui/src/api/client.ts`) gains a required `category: string`.** `ScenarioList.tsx` groups the fetched list by category into sections, in a fixed display order (`CATEGORY_ORDER` constant, mirrored by `harness/tests/test_scenarios.py`'s `_KNOWN_CATEGORIES` — the two lists must be kept in sync by hand, there's no shared source of truth between the Python test and the TypeScript component). "Custom" is always rendered last and always rendered even when empty, with a short empty-state message, so the feature is discoverable before anyone has actually written a custom scenario yet. Any scenario whose `category` isn't one of the 5 known built-ins (a typo, or a deliberately different value) also falls into "Custom" — the bucket is a catch-all for "not one of ours," not just for a literal absent field.

## Consequences

**Positive:**
- Zero new backend infrastructure — `category` is read the same way `name`/`description`/`config` already are, defaulted inline at the same call site.
- The empty-but-visible "Custom" section makes "you can write your own scenario" a discoverable fact of the UI rather than something only documented in `CLAUDE.md`'s Scenario YAML Format section.
- `test_scenario_declares_known_category` (`harness/tests/test_scenarios.py`) catches a built-in scenario silently drifting to an unrecognized category string (typo, or a copy-pasted category from a future addition) before it ships.

**Negative:**
- The category-order lists live in two places (`ui/src/components/ScenarioList.tsx`'s `CATEGORY_ORDER` and `harness/tests/test_scenarios.py`'s `_KNOWN_CATEGORIES`) with no shared source of truth across the Python/TypeScript boundary — adding a 6th built-in category means remembering to update both by hand. Acceptable at 5 categories; would be worth deriving one from the other (or a small shared JSON) if this grows much further.
- No UI affordance yet for a user to *set* a scenario's category without hand-editing YAML — appropriate for now since MaaS:PAL has no scenario-authoring UI at all, just a YAML-drop-in convention.

**Update (2026-10-05):** "Custom" is now a label as well as a category. `GET /api/scenarios` sends `custom: true` for any scenario not in `BUILTIN_SCENARIOS` (`api/routes/scenarios.py`, which also owns `KNOWN_CATEGORIES` now — `test_scenarios.py` imports it). A custom scenario whose `category:` is a built-in one is listed there, ahead of the built-ins; otherwise it goes in the Custom category, which is listed first rather than last and only shown when non-empty. The list became a catalog (`src/app/components/ScenarioCatalog.tsx`) on its own **Scenarios** page, with search and category / source / type filters kept in the URL.

**Neutral:**
- This is purely a display/organization change — it doesn't affect `POST /api/runs`, task execution, or assertion evaluation in any way.
