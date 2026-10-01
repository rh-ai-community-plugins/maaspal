import { useMemo, useRef, useState } from 'react';
import type { TrafficOutcome, TrafficPoint } from '../api/client';

// Cumulative successful tokens over time for one send_requests burst, with
// each non-OK request marked where it happened and the configured token
// limit as a reference line — "how much got through before MaaS throttled
// me?" in one picture. Hand-rolled SVG (no chart library in this app).

const LINE_COLOR = '#1565c0'; // the app's own running/info blue — one series, named by the title
const GRID_COLOR = '#ececec';
const AXIS_TEXT = '#777';
const LIMIT_COLOR = '#555';

// Reserved status colours (dataviz reference palette), each paired with its
// own marker shape and a text label — colour never carries the meaning alone.
const OUTCOMES: Record<Exclude<TrafficOutcome, 'ok'>, { label: string; color: string; shape: 'circle' | 'square' | 'cross' }> = {
  throttled: { label: 'Throttled (429)', color: '#ec835a', shape: 'circle' },
  denied: { label: 'Denied (401/403)', color: '#d03b3b', shape: 'square' },
  server_error: { label: 'Server error (5xx)', color: '#d03b3b', shape: 'cross' },
  error: { label: 'Other error', color: '#d03b3b', shape: 'cross' },
};
const OK_LABEL = 'OK';

// Wide and short: the chart supports the numbers above it, it doesn't lead.
const W = 640;
const H = 150;
const M = { top: 24, right: 16, bottom: 30, left: 52 };

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
  timeline: TrafficPoint[];
  limit?: number | null;
  title?: string;
}

export function TrafficChart({ timeline, limit, title = 'Tokens served over time' }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  const { xMax, yMax, path, present } = useMemo(() => {
    const lastT = timeline.length ? timeline[timeline.length - 1][0] : 0;
    const maxTokens = timeline.reduce((m, p) => Math.max(m, p[1]), 0);
    const yTop = niceMax(Math.max(maxTokens, limit ?? 0) * 1.08);
    const xTop = niceMax(lastT || 1);
    const sx = (t: number) => M.left + (t / xTop) * (W - M.left - M.right);
    const sy = (v: number) => H - M.bottom - (v / yTop) * (H - M.top - M.bottom);
    // Step line: cumulative tokens only ever jump when a request completes.
    let d = `M${sx(0)},${sy(0)}`;
    for (const [t, tokens] of timeline) d += ` H${sx(t)} V${sy(tokens)}`;
    const outcomes = new Set(timeline.map((p) => p[2]).filter((o) => o !== 'ok'));
    return { xMax: xTop, yMax: yTop, path: d, present: outcomes as Set<Exclude<TrafficOutcome, 'ok'>> };
  }, [timeline, limit]);

  if (timeline.length === 0) return null;

  const sx = (t: number) => M.left + (t / xMax) * (W - M.left - M.right);
  const sy = (v: number) => H - M.bottom - (v / yMax) * (H - M.top - M.bottom);

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const t = (((e.clientX - rect.left) / rect.width) * W - M.left) / (W - M.left - M.right) * xMax;
    // Crosshair snaps to the nearest request.
    let best = 0;
    for (let i = 1; i < timeline.length; i++) {
      if (Math.abs(timeline[i][0] - t) < Math.abs(timeline[best][0] - t)) best = i;
    }
    setHoverIdx(best);
  }

  const hover = hoverIdx !== null ? timeline[hoverIdx] : null;
  const hoverLeftPct = hover ? (sx(hover[0]) / W) * 100 : 0;

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
            Configured limit
          </span>
        )}
      </div>
      <div style={{ position: 'relative' }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label={`${title}: ${timeline.length} requests over ${fmt(timeline[timeline.length - 1][0])} seconds, ending at ${fmt(timeline[timeline.length - 1][1])} tokens`}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setHoverIdx(null)}
          style={{ display: 'block', touchAction: 'none' }}
        >
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
              {/* Left end: throttled markers cluster at the right, after the limit is hit. */}
              <text x={M.left + 4} y={sy(limit) - 4} textAnchor="start" fontSize={11} fill={LIMIT_COLOR}>
                limit {fmt(limit)}
              </text>
            </g>
          )}

          <path d={path} fill="none" stroke={LINE_COLOR} strokeWidth={2} strokeLinejoin="round" />

          {timeline.map((p, i) =>
            p[2] === 'ok' ? null : <Marker key={i} x={sx(p[0])} y={sy(p[1])} outcome={p[2]} />,
          )}

          {hover && (
            <g pointerEvents="none">
              <line x1={sx(hover[0])} x2={sx(hover[0])} y1={M.top} y2={H - M.bottom} stroke="#999" strokeWidth={1} />
              <circle cx={sx(hover[0])} cy={sy(hover[1])} r={4} fill={LINE_COLOR} stroke="#fff" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hover && (
          <div
            role="status"
            style={{
              position: 'absolute',
              top: 4,
              left: `${hoverLeftPct}%`,
              transform: hoverLeftPct > 60 ? 'translateX(calc(-100% - 10px))' : 'translateX(10px)',
              background: '#fff',
              border: '1px solid #ddd',
              borderRadius: 4,
              padding: '0.3rem 0.5rem',
              fontSize: '0.75rem',
              boxShadow: '0 2px 6px rgba(0,0,0,0.08)',
              pointerEvents: 'none',
              whiteSpace: 'nowrap',
            }}
          >
            <div style={{ fontWeight: 700, color: '#222' }}>{fmt(hover[1])} tokens</div>
            <div style={{ color: '#666' }}>
              at {fmt(hover[0])}s · {hover[2] === 'ok' ? OK_LABEL : OUTCOMES[hover[2]].label} ·{' '}
              {fmt(hover[3])} ms
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
                <th>Time</th>
                <th>Tokens so far</th>
                <th>Outcome</th>
                <th>Latency</th>
              </tr>
            </thead>
            <tbody>
              {timeline.map((p, i) => (
                <tr key={i}>
                  <td>{fmt(p[0])}s</td>
                  <td>{fmt(p[1])}</td>
                  <td>{p[2] === 'ok' ? OK_LABEL : OUTCOMES[p[2]].label}</td>
                  <td>{fmt(p[3])} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </figure>
  );
}
