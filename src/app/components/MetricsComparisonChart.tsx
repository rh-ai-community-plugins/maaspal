import { useRef, useState } from 'react';
import type { MetricsChart } from '../api/client';
import { COLOR } from '../styles/colors';

// What MaaS reported vs what this run actually sent, over time, on one axis.
// The gap closing is Prometheus catching up (it scrapes every ~30 s); a gap
// that never closes is the real mismatch. Hand-rolled SVG like TrafficChart.

const MAAS_COLOR = COLOR.chart.series; // solid — the thing under test
const HARNESS_COLOR = COLOR.chart.reference; // dashed — the reference
const GRID_COLOR = COLOR.chart.grid;
const AXIS_TEXT = COLOR.chart.axis;

const W = 400;
const H = 170;
const M = { top: 24, right: 12, bottom: 28, left: 48 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].find((m) => m * pow >= v) ?? 10;
  return step * pow;
}

function fmt(v: number): string {
  return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1);
}

export function MetricsComparisonChart({ chart }: { chart: MetricsChart }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const pts = chart.points;
  if (pts.length === 0) return null;

  const xMax = niceMax(pts[pts.length - 1][0] || 1);
  const yMax = niceMax(Math.max(...pts.map((p) => Math.max(p[1], p[2]))) * 1.08);
  const sx = (t: number) => M.left + (t / xMax) * (W - M.left - M.right);
  const sy = (v: number) => H - M.bottom - (v / yMax) * (H - M.top - M.bottom);
  const line = (i: 1 | 2) =>
    pts.map((p, j) => `${j === 0 ? 'M' : 'L'}${sx(p[0]).toFixed(1)},${sy(p[i]).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];
  const hover = hoverIdx !== null ? pts[hoverIdx] : null;
  const hoverLeftPct = hover ? (sx(hover[0]) / W) * 100 : 0;

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const t = ((((e.clientX - rect.left) / rect.width) * W - M.left) / (W - M.left - M.right)) * xMax;
    let best = 0;
    for (let i = 1; i < pts.length; i++) {
      if (Math.abs(pts[i][0] - t) < Math.abs(pts[best][0] - t)) best = i;
    }
    setHoverIdx(best);
  }

  return (
    <figure style={{ margin: 0 }}>
      <figcaption style={{ fontSize: '0.8rem', fontWeight: 600, color: COLOR.text, marginBottom: '0.2rem' }}>
        {chart.title}
      </figcaption>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.9rem', fontSize: '0.74rem', color: COLOR.subtle }}>
        <span>
          <svg width={18} height={8} aria-hidden="true" style={{ verticalAlign: 'middle' }}>
            <line x1={0} y1={4} x2={18} y2={4} stroke={MAAS_COLOR} strokeWidth={2} />
          </svg>{' '}
          {chart.maas_label}: <strong>{fmt(last[1])}</strong>
        </span>
        <span>
          <svg width={18} height={8} aria-hidden="true" style={{ verticalAlign: 'middle' }}>
            <line x1={0} y1={4} x2={18} y2={4} stroke={HARNESS_COLOR} strokeWidth={2} strokeDasharray="4 3" />
          </svg>{' '}
          {chart.harness_label}: <strong>{fmt(last[2])}</strong>
        </span>
      </div>
      <div style={{ position: 'relative' }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label={`${chart.title}: ${chart.maas_label} ${fmt(last[1])}, ${chart.harness_label} ${fmt(last[2])}`}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setHoverIdx(null)}
          style={{ display: 'block', touchAction: 'none' }}
        >
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <line x1={M.left} x2={W - M.right} y1={sy(yMax * f)} y2={sy(yMax * f)} stroke={GRID_COLOR} />
              <text x={M.left - 6} y={sy(yMax * f)} dy="0.32em" textAnchor="end" fontSize={10} fill={AXIS_TEXT}>
                {fmt(yMax * f)}
              </text>
              <text x={sx(xMax * f)} y={H - M.bottom + 16} textAnchor="middle" fontSize={10} fill={AXIS_TEXT}>
                {fmt(xMax * f)}s
              </text>
            </g>
          ))}
          <text x={M.left - 6} y={10} textAnchor="end" fontSize={10} fill={AXIS_TEXT}>
            {chart.unit}
          </text>
          <path d={line(2)} fill="none" stroke={HARNESS_COLOR} strokeWidth={2} strokeDasharray="4 3" />
          <path d={line(1)} fill="none" stroke={MAAS_COLOR} strokeWidth={2} strokeLinejoin="round" />
          {hover && (
            <g pointerEvents="none">
              <line x1={sx(hover[0])} x2={sx(hover[0])} y1={M.top} y2={H - M.bottom} stroke={COLOR.muted} />
              <circle cx={sx(hover[0])} cy={sy(hover[1])} r={4} fill={MAAS_COLOR} stroke={COLOR.surface} strokeWidth={2} />
              <circle cx={sx(hover[0])} cy={sy(hover[2])} r={4} fill={HARNESS_COLOR} stroke={COLOR.surface} strokeWidth={2} />
            </g>
          )}
        </svg>
        {hover && (
          <div
            role="status"
            className="maaspal-chart-tooltip"
            style={{
              left: `${hoverLeftPct}%`,
              transform: hoverLeftPct > 55 ? 'translateX(calc(-100% - 10px))' : 'translateX(10px)',
            }}
          >
            <div style={{ color: COLOR.muted }}>at {fmt(hover[0])}s</div>
            <div>
              <strong>{fmt(hover[1])}</strong> {chart.maas_label.toLowerCase()}
            </div>
            <div>
              <strong>{fmt(hover[2])}</strong> {chart.harness_label.toLowerCase()}
            </div>
          </div>
        )}
      </div>
      <button
        type="button"
        className="maaspal-assertion-card__details-toggle"
        onClick={() => setShowTable((v) => !v)}
        aria-expanded={showTable}
      >
        {showTable ? 'Hide data table' : 'Show data table'}
      </button>
      {showTable && (
        <div className="maaspal-table-scroll" style={{ maxHeight: 200, overflowY: 'auto' }}>
          <table className="maaspal-detail-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>{chart.maas_label}</th>
                <th>{chart.harness_label}</th>
              </tr>
            </thead>
            <tbody>
              {pts.map((p, i) => (
                <tr key={i}>
                  <td>{fmt(p[0])}s</td>
                  <td>{fmt(p[1])}</td>
                  <td>{fmt(p[2])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </figure>
  );
}
