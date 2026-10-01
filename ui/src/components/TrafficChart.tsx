import { useMemo, useRef, useState } from 'react';
import type { TrafficOutcome, TrafficPoint } from '../api/client';

// Cumulative successful tokens over time for one or more send_requests
// bursts, with each non-OK request marked where it happened and the
// configured token limit as a reference line — "how much got through before
// MaaS throttled me?" in one picture. Several bursts (a chart_group, e.g.
// before/after waiting out a rate-limit window) share one timeline: each
// starts from 0 again (a new window or a different user's budget), and the
// time between them is shaded. Hand-rolled SVG (no chart library in this app).

const LINE_COLOR = '#1565c0'; // the app's own running/info blue — one measure, named by the title
const GRID_COLOR = '#ececec';
const AXIS_TEXT = '#777';
const LIMIT_COLOR = '#555';
const GAP_FILL = '#f2f2f2';

// Reserved status colours (dataviz reference palette), each paired with its
// own marker shape and a text label — colour never carries the meaning alone.
const OUTCOMES: Record<
  Exclude<TrafficOutcome, 'ok'>,
  { label: string; color: string; shape: 'circle' | 'square' | 'cross' | 'diamond' }
> = {
  throttled: { label: 'Throttled (429)', color: '#ec835a', shape: 'circle' },
  denied: { label: 'Denied (401/403)', color: '#d03b3b', shape: 'square' },
  not_found: { label: 'Not found (404)', color: '#d03b3b', shape: 'diamond' },
  server_error: { label: 'Server error (5xx)', color: '#d03b3b', shape: 'cross' },
  error: { label: 'Other error', color: '#d03b3b', shape: 'cross' },
};
const OK_LABEL = 'OK';

// Wide and short: the chart supports the numbers above it, it doesn't lead.
const W = 640;
const H = 150;
const M = { top: 24, right: 16, bottom: 30, left: 52 };
// A pause between bursts shorter than this isn't worth calling out.
const MIN_GAP_S = 1;

export interface TrafficSegment {
  label?: string;
  // Seconds after the first segment started.
  offset: number;
  timeline: TrafficPoint[];
}

interface FlatPoint {
  x: number;
  tokens: number;
  outcome: TrafficOutcome;
  latency: number;
  segment: number;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * pow >= v) ?? 10;
  return step * pow;
}

function ticks(max: number, count = 4): number[] {
  return Array.from({ length: count + 1 }, (_, i) => (max / count) * i);
}

function fmt(v: number): string {
  return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1);
}

function Marker({ x, y, outcome }: { x: number; y: number; outcome: Exclude<TrafficOutcome, 'ok'> }) {
  const { color, shape } = OUTCOMES[outcome];
  // 2px surface ring keeps overlapping markers legible against the line.
  if (shape === 'square') {
    return <rect x={x - 4} y={y - 4} width={8} height={8} rx={1.5} fill={color} stroke="#fff" strokeWidth={2} />;
  }
  if (shape === 'diamond') {
    return (
      <rect
        x={x - 4}
        y={y - 4}
        width={8}
        height={8}
        fill={color}
        stroke="#fff"
        strokeWidth={2}
        transform={`rotate(45 ${x} ${y})`}
      />
    );
  }
  if (shape === 'cross') {
    return (
      <g stroke={color} strokeWidth={2.5} strokeLinecap="round">
        <line x1={x - 4} y1={y - 4} x2={x + 4} y2={y + 4} />
        <line x1={x - 4} y1={y + 4} x2={x + 4} y2={y - 4} />
      </g>
    );
  }
  return <circle cx={x} cy={y} r={4.5} fill={color} stroke="#fff" strokeWidth={2} />;
}

function LegendSwatch({ outcome }: { outcome: Exclude<TrafficOutcome, 'ok'> }) {
  return (
    <svg width={12} height={12} aria-hidden="true" style={{ verticalAlign: 'middle' }}>
      <Marker x={6} y={6} outcome={outcome} />
    </svg>
  );
}

interface Props {
  /** One burst… */
  timeline?: TrafficPoint[];
  /** …or several on one timeline. */
  segments?: TrafficSegment[];
  limit?: number | null;
  title?: string;
}

export function TrafficChart({ timeline, segments, limit, title = 'Tokens served over time' }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  const segs: TrafficSegment[] = useMemo(
    () => segments ?? (timeline ? [{ offset: 0, timeline }] : []),
    [segments, timeline],
  );

  const { points, xMax, yMax, present, gaps } = useMemo(() => {
    const flat: FlatPoint[] = segs.flatMap((seg, i) =>
      seg.timeline.map(([t, tokens, outcome, latency]) => ({
        x: seg.offset + t,
        tokens,
        outcome,
        latency,
        segment: i,
      })),
    );
    const lastX = flat.length ? Math.max(...flat.map((p) => p.x)) : 0;
    const maxTokens = flat.reduce((m, p) => Math.max(m, p.tokens), 0);
    const gapList: { from: number; to: number }[] = [];
    for (let i = 1; i < segs.length; i++) {
      const prev = segs[i - 1];
      const prevEnd = prev.offset + (prev.timeline.length ? prev.timeline[prev.timeline.length - 1][0] : 0);
      if (segs[i].offset - prevEnd >= MIN_GAP_S) gapList.push({ from: prevEnd, to: segs[i].offset });
    }
    return {
      points: flat,
      xMax: niceMax(lastX || 1),
      yMax: niceMax(Math.max(maxTokens, limit ?? 0) * 1.08),
      present: new Set(flat.map((p) => p.outcome).filter((o) => o !== 'ok')) as Set<Exclude<TrafficOutcome, 'ok'>>,
      gaps: gapList,
    };
  }, [segs, limit]);

  if (points.length === 0) return null;
  const multi = segs.length > 1;

  const sx = (t: number) => M.left + (t / xMax) * (W - M.left - M.right);
  const sy = (v: number) => H - M.bottom - (v / yMax) * (H - M.top - M.bottom);

  // Step line per segment: cumulative tokens only jump when a request completes.
  const paths = segs.map((seg) => {
    let d = `M${sx(seg.offset)},${sy(0)}`;
    for (const [t, tokens] of seg.timeline) d += ` H${sx(seg.offset + t)} V${sy(tokens)}`;
    return d;
  });

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const t = ((((e.clientX - rect.left) / rect.width) * W - M.left) / (W - M.left - M.right)) * xMax;
    // Crosshair snaps to the nearest request.
    let best = 0;
    for (let i = 1; i < points.length; i++) {
      if (Math.abs(points[i].x - t) < Math.abs(points[best].x - t)) best = i;
    }
    setHoverIdx(best);
  }

  const hover = hoverIdx !== null ? points[hoverIdx] : null;
  const hoverLeftPct = hover ? (sx(hover.x) / W) * 100 : 0;
  const last = points[points.length - 1];

  return (
    <figure style={{ margin: 0 }}>
      <figcaption style={{ fontSize: '0.8rem', fontWeight: 600, color: '#333', marginBottom: '0.25rem' }}>
        {title}
      </figcaption>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.9rem', fontSize: '0.74rem', color: '#555', marginBottom: '0.25rem' }}>
        {[...present].map((o) => (
          <span key={o}>
            <LegendSwatch outcome={o} /> {OUTCOMES[o].label}
          </span>
        ))}
        {limit != null && (
          <span>
            <svg width={18} height={8} aria-hidden="true" style={{ verticalAlign: 'middle' }}>
              <line x1={0} y1={4} x2={18} y2={4} stroke={LIMIT_COLOR} strokeWidth={1.5} strokeDasharray="4 3" />
            </svg>{' '}
            Configured limit ({fmt(limit)})
          </span>
        )}
        {gaps.length > 0 && (
          <span>
            <svg width={14} height={10} aria-hidden="true" style={{ verticalAlign: 'middle' }}>
              <rect width={14} height={10} fill={GAP_FILL} stroke="#ddd" />
            </svg>{' '}
            Waiting between bursts
          </span>
        )}
      </div>
      <div style={{ position: 'relative' }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label={`${title}: ${points.length} requests over ${fmt(last.x)} seconds${
            multi ? ` in ${segs.length} bursts` : ''
          }, ending at ${fmt(last.tokens)} tokens`}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setHoverIdx(null)}
          style={{ display: 'block', touchAction: 'none' }}
        >
          {gaps.map((g, i) => (
            <g key={`gap${i}`}>
              <rect
                x={sx(g.from)}
                y={M.top}
                width={Math.max(sx(g.to) - sx(g.from), 1)}
                height={H - M.top - M.bottom}
                fill={GAP_FILL}
              />
              <text
                x={(sx(g.from) + sx(g.to)) / 2}
                y={M.top + 12}
                textAnchor="middle"
                fontSize={11}
                fill={AXIS_TEXT}
              >
                waiting {fmt(Math.round(g.to - g.from))}s
              </text>
            </g>
          ))}
          {ticks(yMax).map((v) => (
            <g key={`y${v}`}>
              <line x1={M.left} x2={W - M.right} y1={sy(v)} y2={sy(v)} stroke={GRID_COLOR} />
              <text x={M.left - 6} y={sy(v)} dy="0.32em" textAnchor="end" fontSize={11} fill={AXIS_TEXT}>
                {fmt(v)}
              </text>
            </g>
          ))}
          {ticks(xMax).map((v) => (
            <text key={`x${v}`} x={sx(v)} y={H - M.bottom + 16} textAnchor="middle" fontSize={11} fill={AXIS_TEXT}>
              {fmt(v)}s
            </text>
          ))}
          <text x={M.left - 6} y={10} textAnchor="end" fontSize={11} fill={AXIS_TEXT}>
            tokens
          </text>

          {limit != null && (
            <g>
              <line
                x1={M.left}
                x2={W - M.right}
                y1={sy(limit)}
                y2={sy(limit)}
                stroke={LIMIT_COLOR}
                strokeWidth={1.5}
                strokeDasharray="4 3"
              />
              {/* Single burst only — on a multi-burst timeline the bursts are short and
                  any spot on the line is crowded; the legend carries the value there.
                  Left end: throttled markers cluster at the right. */}
              {!multi && (
                <text x={M.left + 4} y={sy(limit) - 4} textAnchor="start" fontSize={11} fill={LIMIT_COLOR}>
                  limit {fmt(limit)}
                </text>
              )}
            </g>
          )}

          {paths.map((d, i) => (
            <path key={`p${i}`} d={d} fill="none" stroke={LINE_COLOR} strokeWidth={2} strokeLinejoin="round" />
          ))}
          {multi &&
            segs.map((seg, i) =>
              seg.label ? (
                // Above the plot, where no data is drawn; the last one is
                // right-aligned so it can't run off the edge.
                <text
                  key={`l${i}`}
                  x={i === segs.length - 1 && i > 0 ? W - M.right : sx(seg.offset)}
                  y={M.top - 8}
                  textAnchor={i === segs.length - 1 && i > 0 ? 'end' : 'start'}
                  fontSize={11}
                  fontWeight={600}
                  fill="#333"
                >
                  {seg.label}
                </text>
              ) : null,
            )}

          {points.map((p, i) =>
            p.outcome === 'ok' ? null : <Marker key={i} x={sx(p.x)} y={sy(p.tokens)} outcome={p.outcome} />,
          )}

          {hover && (
            <g pointerEvents="none">
              <line x1={sx(hover.x)} x2={sx(hover.x)} y1={M.top} y2={H - M.bottom} stroke="#999" strokeWidth={1} />
              <circle cx={sx(hover.x)} cy={sy(hover.tokens)} r={4} fill={LINE_COLOR} stroke="#fff" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hover && (
          <div
            role="status"
            className="maaspal-chart-tooltip"
            style={{
              left: `${hoverLeftPct}%`,
              transform: hoverLeftPct > 60 ? 'translateX(calc(-100% - 10px))' : 'translateX(10px)',
            }}
          >
            <div style={{ fontWeight: 700, color: '#222' }}>{fmt(hover.tokens)} tokens</div>
            <div style={{ color: '#666' }}>
              {multi && segs[hover.segment].label ? `${segs[hover.segment].label} · ` : ''}
              at {fmt(hover.x)}s · {hover.outcome === 'ok' ? OK_LABEL : OUTCOMES[hover.outcome].label} ·{' '}
              {fmt(hover.latency)} ms
            </div>
          </div>
        )}
      </div>
      <button
        type="button"
        className="maaspal-assertion-card__details-toggle"
        onClick={() => setShowTable((v) => !v)}
        aria-expanded={showTable}
        style={{ marginTop: '0.25rem' }}
      >
        {showTable ? 'Hide data table' : 'Show data table'}
      </button>
      {showTable && (
        <div className="maaspal-table-scroll" style={{ maxHeight: 220, overflowY: 'auto' }}>
          <table className="maaspal-detail-table">
            <thead>
              <tr>
                {multi && <th>Burst</th>}
                <th>Time</th>
                <th>Tokens so far</th>
                <th>Outcome</th>
                <th>Latency</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p, i) => (
                <tr key={i}>
                  {multi && <td>{segs[p.segment].label ?? p.segment + 1}</td>}
                  <td>{fmt(p.x)}s</td>
                  <td>{fmt(p.tokens)}</td>
                  <td>{p.outcome === 'ok' ? OK_LABEL : OUTCOMES[p.outcome].label}</td>
                  <td>{fmt(p.latency)} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </figure>
  );
}
