# MaaS:PAL colours and components

How MaaS:PAL looks in both the light and the dark dashboard theme, and how to
keep it that way. MaaS:PAL has its own palette (red `#c62828`). It doesn't
try to match RHOAI or OpenShift, only to be consistent with itself.

## Rules

1. **No colour literals in components.** Every colour is a token from
   [`src/app/styles/tokens.css`](../../src/app/styles/tokens.css). In CSS,
   use `var(--maaspal-…)`. In TSX (inline styles, SVG), use `COLOR.…`,
   `toneColor()` or `toneBg()` from
   [`src/app/styles/colors.ts`](../../src/app/styles/colors.ts).
   `src/app/styles/noHardcodedColors.test.ts` fails the build on a hex,
   `rgb()` or `hsl()` value anywhere else.
2. **Statuses go through [`src/app/status.ts`](../../src/app/status.ts).**
   It decides the tone, icon and label of every run, task, check and cleanup
   status. Don't keep a colour table in a component.
3. **The same job uses the same component.** See the component guide below.
4. **Colour is never the only signal.** Statuses also carry an icon (✓ ✗ ⊘ ◎ ○)
   or a word. Chart outcomes also carry a marker shape.
5. **A new colour is a new token.** Add it to `tokens.css` with a value for
   *both* themes, add it to `COLOR` if TSX needs it, and list it here.

## How theming works

The dashboard puts `pf-v6-theme-dark` on `<html>` in dark mode. `tokens.css`
defines every token on `.maaspal-plugin`, `.maaspal-modal` and
`.maaspal-popover`, and redefines the ones that change under
`:root.pf-v6-theme-dark`. Modals and popovers render in `document.body`,
outside the plugin root, so give them the `maaspal-modal` or
`maaspal-popover` class.

- **Neutrals** (surfaces, text, borders) alias PatternFly's semantic tokens,
  so panels match whatever the dashboard draws them on: a page (`#fff` or
  `#292929`), a modal (`#383838` in dark), or a popover.
- **Brand, status, label and chart colours** are MaaS:PAL's own, with one
  value per theme. Every text colour is at least 4.5:1 on all light surfaces
  (`#fff`, `#f2f2f2`) and all dark ones (`#1f1f1f`, `#292929`, `#383838`).

## Palette

### Neutrals (follow the dashboard)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--maaspal-surface` | PF primary bg (`#fff`) | PF primary bg (`#292929`) | Page, panels, cards |
| `--maaspal-surface-raised` | PF secondary bg | PF secondary bg | Inline code, active filter row |
| `--maaspal-surface-floating` | PF floating bg | PF floating bg | Chart hover tooltips |
| `--maaspal-text` | PF regular | PF regular | Body text, values |
| `--maaspal-text-subtle` | PF subtle | PF subtle | Secondary text, descriptions |
| `--maaspal-text-muted` | `#6a6e73` | `#a3a3a3` | Hints, "—", empty states, axis labels |
| `--maaspal-border` | PF default border | PF default border | Panel and card borders |
| `--maaspal-border-subtle` | border at 55% | border at 55% | Dividers, table rows, progress tracks |
| `--maaspal-hover` | PF secondary hover | PF secondary hover | Hovered list rows |
| `--maaspal-shadow` | `rgba(0,0,0,.12)` | `rgba(0,0,0,.45)` | Card and tooltip shadows |

### Brand

| Token | Light | Dark | Use |
|---|---|---|---|
| `--maaspal-accent` | `#c62828` | `#c62828` | Primary buttons, card stripes, active filter |
| `--maaspal-accent-hover` | `#b71c1c` | `#d43a3a` | Primary button hover |
| `--maaspal-accent-active` | `#9b1818` | `#e04b4b` | Primary button pressed |
| `--maaspal-on-accent` | `#fff` | `#fff` | Text on the accent (5.6:1) |
| `--maaspal-custom` | `#6b4fbb` | `#b3a3ee` | Custom scenario stripe |

### Status tones

Each tone has `--maaspal-<tone>` (icons, stripes, status text) and
`--maaspal-<tone>-bg` (a tint of the tone mixed into the surface: 10% in light,
14% in dark).

| Tone | Light | Dark | Means |
|---|---|---|---|
| `success` | `#2e7d32` | `#63c174` | Passed, done, removed / restored / revoked |
| `danger` | `#c62828` | `#f78a8a` | Failed, cleanup failed, errors |
| `warning` | `#a35200` | `#f0ab4b` | Stopped / cancelled, left in place |
| `info` | `#1565c0` | `#7aaef0` | Running, findings, links inside our own widgets |
| `neutral` | `#6a6e73` | `#a3a3a3` | Pending, not started, exists |

`danger` and `accent` are the same red in light mode on purpose. Use
`danger` for *something went wrong* and `accent` for *brand / primary action*,
since they differ in dark mode.

### Status mapping (`status.ts`)

| Status | Tone | Icon | PF `Label` colour |
|---|---|---|---|
| `PASS`, `DONE`, `PASSING` | success | ✓ | green |
| `FAIL`, `FAILING` | danger | ✗ | red |
| `CANCELLED` | warning | ⊘ | orange |
| `RUNNING` | info | ◎ | blue |
| `PENDING` (and anything else) | neutral | ○ | grey |

| Resource status | Tone | Label |
|---|---|---|
| `active` | neutral | exists |
| `removed` / `restored` / `revoked` | success | removed ✓ / restored ✓ / revoked ✓ |
| `cleanup failed` | danger | cleanup failed ✗ |
| `left in place` | warning | left in place |

### Charts

| Token | Light | Dark | Role |
|---|---|---|---|
| `--maaspal-chart-series` | = info | = info | The measured line (tokens served, MaaS-reported) |
| `--maaspal-chart-reference` | `#555` | `#b8b8b8` | What MaaS:PAL sent (dashed) and the limit line |
| `--maaspal-chart-grid` | text at 10% | text at 10% | Gridlines |
| `--maaspal-chart-axis` | = text-muted | = text-muted | Axis labels |
| `--maaspal-chart-gap` | text at 6% | text at 6% | Shaded waits between bursts |
| `--maaspal-chart-throttled` | `#b04a12` | `#f2925f` | 429 markers (circle) |
| `--maaspal-chart-error` | = danger | = danger | Other failures (square 401/403, diamond 404, cross 5xx) |

Markers get a `--maaspal-surface` outline so they stand out on the line in
both themes.

### Code and logs (dark in both themes)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--maaspal-log-bg` | `#1a1a1a` | `#151515` | Log block background |
| `--maaspal-log-border` | `#333` | `#444` | Log block border |
| `--maaspal-log-text` | `#d4d4d4` | `#d4d4d4` | Log text |

The YAML viewer (`RawYamlModal`, PatternFly `CodeEditor` with `isDarkTheme`)
is dark in both themes too, matching the logs.

## Component guide

| Need | Use |
|---|---|
| Page title + one-line explanation (+ action) | `components/PageIntro.tsx` |
| Small uppercase section label | `.maaspal-section-heading` |
| Bordered content block on the run page | `.maaspal-panel` (+ `.maaspal-panel__header`, `.maaspal-panel__title`) |
| Clickable tile | PF `Card` (see `.maaspal-catalog-card`) |
| Status badge in a table | PF `Label` with `color={statusLabelColor(status)}` |
| Step facts in "What happened" | Compact PF `Label`: sent from the pod (grey outline) / the browser (blue); endpoint + streaming (purple outline) |
| Raw error text (error samples) | `<code>` with `white-space: pre-wrap` — shown whole, never truncated |
| Status icon or text | `statusStyle(status)` → `toneColor(tone)` + `icon` |
| Tinted status block (verdict, check card, task chip) | `toneColor(tone)` for the stripe/border, `toneBg(tone)` for the fill |
| Muted text, "—" | `.maaspal-text-muted` (or `COLOR.muted` in an inline style) |
| Empty state line ("No models found.") | `.maaspal-empty` |
| Form controls | PF `TextInput`, `FormSelect` + `FormSelectOption`, `Switch` (never native `<input>`/`<select>`) |
| Inline object names | `<code>` (styled for both themes) |
| Logs | `LogStream` (PF `CodeBlock` in `.maaspal-log-block`) |
| Charts | `TrafficChart`, `MetricsComparisonChart`; colours from `COLOR.chart` |
| Wide table | Wrap it in `.maaspal-table-scroll` |

## Checking both themes

- **In the dashboard:** use the theme toggle in the masthead (sun / moon).
- **In standalone dev** (`make dev-standalone`): run
  `document.documentElement.classList.toggle('pf-v6-theme-dark')` in the
  browser console.
- **Pages to look at:**
  - Scenarios: the catalog and the launch form, with Advanced open.
  - Runs.
  - Run pages: a passed run, a failed run with cleanup problems, a running
    run, a step-load run, a metrics-comparison run, a run with a finding, and
    Run Settings.
  - Every MaaS overview tab, and a View YAML modal.
- **Watch for:**
  - Text that disappears (dark on dark, light on light).
  - Light panels in dark mode.
  - Markers or lines that vanish into the background.
