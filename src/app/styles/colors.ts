// Typed references to the colour tokens in tokens.css, for inline styles and
// SVG (`style={{ fill: COLOR.chart.series }}`). Never put a hex/rgb literal in a
// component: add a token to tokens.css (both themes) and reference it here.
// See docs/design/colors-and-components.md.

const v = (name: string) => `var(--maaspal-${name})`;

export const COLOR = {
  surface: v('surface'),
  surfaceRaised: v('surface-raised'),
  surfaceFloating: v('surface-floating'),
  text: v('text'),
  subtle: v('text-subtle'),
  muted: v('text-muted'),
  border: v('border'),
  borderSubtle: v('border-subtle'),
  accent: v('accent'),
  custom: v('custom'),
  chart: {
    series: v('chart-series'),
    reference: v('chart-reference'),
    grid: v('chart-grid'),
    axis: v('chart-axis'),
    gap: v('chart-gap'),
    throttled: v('chart-throttled'),
    error: v('chart-error'),
  },
} as const;

/** The five status tones; each has `--maaspal-<tone>` and `--maaspal-<tone>-bg`. */
export type Tone = 'success' | 'danger' | 'warning' | 'info' | 'neutral';

export const toneColor = (tone: Tone) => v(tone);
export const toneBg = (tone: Tone) => v(`${tone}-bg`);
