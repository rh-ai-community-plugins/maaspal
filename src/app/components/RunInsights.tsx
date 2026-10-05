import { useState } from 'react';
import { Button } from '@patternfly/react-core';
import type {
  ErrorSample,
  LoadStage,
  MetricsChart,
  RunFinding,
  RunResource,
  RunTable,
  RunVerdict,
  TaskProgressEntry,
  TrafficBurst,
  TrafficSummary,
} from '../api/client';
import { formatTaskName } from '../scenarioTitles';
import { MetricsComparisonChart } from './MetricsComparisonChart';
import { TrafficChart } from './TrafficChart';
import { RESOURCE_STATUS, statusStyle } from '../status';
import { COLOR, toneColor } from '../styles/colors';

const VERDICT_LABEL: Record<RunVerdict['status'], string> = {
  PASS: 'Behaving as expected',
  FAIL: 'Not behaving as expected',
  CANCELLED: 'Stopped',
};

/** One plain-language sentence summing the run up, written by the harness
 * at the end of the run (harness/runner.py:_render_verdict). */
export function VerdictBanner({ verdict }: { verdict: RunVerdict }) {
  const status = verdict.status in VERDICT_LABEL ? verdict.status : 'FAIL';
  const { tone, icon } = statusStyle(status);
  const style = { color: toneColor(tone), icon, label: VERDICT_LABEL[status] };
  return (
    <div
      className="maaspal-verdict"
      style={{ '--verdict-color': style.color } as React.CSSProperties}
      role="status"
    >
      <span className="maaspal-verdict__icon" aria-hidden="true">{style.icon}</span>
      <div>
        <div className="maaspal-verdict__text">
          <strong>{style.label}.</strong> {verdict.text}
        </div>
        {verdict.checks_total > 0 && (
          <div className="maaspal-verdict__checks">
            {verdict.checks_passed} of {verdict.checks_total} checks passed
          </div>
        )}
      </div>
    </div>
  );
}

function n(v: number | undefined, digits = 0): string {
  if (v === undefined || v === null) return '—';
  return digits ? v.toFixed(digits) : Math.round(v).toLocaleString();
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="maaspal-stat__label">{label}</div>
      <div className="maaspal-stat__value">{value}</div>
      {sub && <div className="maaspal-stat__sub">{sub}</div>}
    </div>
  );
}

function outcomeBreakdown(s: TrafficSummary): string {
  const parts = [`${n(s.success_count)} OK`];
  if (s.rate_limited_count) parts.push(`${n(s.rate_limited_count)} throttled`);
  if (s.unauthorized_count) parts.push(`${n(s.unauthorized_count)} denied`);
  if (s.not_found_count) parts.push(`${n(s.not_found_count)} not found (404)`);
  if (s.server_error_count) parts.push(`${n(s.server_error_count)} 5xx`);
  if (s.other_error_count) parts.push(`${n(s.other_error_count)} other`);
  return parts.join(' · ');
}

/** The numbers a user needs to judge a burst themselves — the same values the
 * assertions check, plus context (tokens split, latency spread, retries). */
/** Why requests failed — the most common error messages, so a burst that
 * failed for a reason the counts can't name explains itself. */
function ErrorSamples({ s }: { s: TrafficSummary }) {
  return <ErrorList samples={s.error_samples ?? []} label="Most common errors" />;
}

/** What the attempts the SDK retried on its own got back — failures callers
 * never saw, but the gateway did. */
function RetriedAttempts({ s }: { s: TrafficSummary }) {
  const retried = s.retried_failed_attempts ?? 0;
  const samples = s.attempt_error_samples ?? [];
  if (retried <= 0 || samples.length === 0) return null;
  return (
    <>
      <p className="maaspal-stages__retries">
        The SDK retried {n(retried)} failed attempt{retried === 1 ? '' : 's'} on its own, as a real client would —
        callers never saw {retried === 1 ? 'it' : 'them'}. What those attempts got back:
      </p>
      <ErrorList samples={samples} label="Failed attempts the SDK retried" />
    </>
  );
}

function ErrorList({ samples, label }: { samples: ErrorSample[]; label: string }) {
  if (samples.length === 0) return null;
  return (
    <ul className="maaspal-error-samples" aria-label={label}>
      {samples.map((e) => (
        <li key={e.message}>
          <strong>{n(e.count)}×</strong> <code>{e.message}</code>
          {e.median_ms !== undefined && (
            <span className="maaspal-error-samples__timing">
              {' '}
              after {n(e.median_ms)} ms
              {e.p10_ms !== undefined && e.p90_ms !== undefined && e.p90_ms > e.p10_ms
                ? ` (most ${n(e.p10_ms)}–${n(e.p90_ms)} ms)`
                : ''}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function TrafficPanel({ burst }: { burst: TrafficBurst }) {
  // Open as a chart when the scenario marked this burst as the one that
  // answers its question; any burst can be switched either way.
  const [expanded, setExpanded] = useState(!!burst.chart);
  const s = burst.summary;
  if (s.stages?.length) return <StagesPanel burst={burst} />;
  if (!expanded) return <TrafficLine burst={burst} onToggle={() => setExpanded(true)} />;
  const retries =
    s.http_attempts !== undefined && s.total_requests !== undefined && s.http_attempts > s.total_requests
      ? s.http_attempts - s.total_requests
      : 0;
  const throttled = s.tokens_before_first_429 !== undefined;
  const heading = burst.label ? `${formatTaskName(burst.task)} — ${burst.label}` : formatTaskName(burst.task);
  return (
    <section className="maaspal-panel" aria-label={`Traffic: ${heading}`}>
      <div className="maaspal-panel__header">
        <p className="maaspal-panel__title">Traffic · {heading}</p>
        <Button variant="link" isInline onClick={() => setExpanded(false)} aria-expanded>
          Show summary
        </Button>
      </div>
      <div className="maaspal-stat-row">
        <Stat
          label="Requests"
          value={`${n(s.total_requests)}${burst.planned ? ` / ${n(burst.planned)}` : ''}`}
          sub={outcomeBreakdown(s)}
        />
        <Stat
          label="Tokens served"
          value={n(s.total_tokens_sent)}
          sub={`${n(s.prompt_tokens_sent)} prompt · ${n(s.completion_tokens_sent)} completion`}
        />
        {burst.limit != null && <Stat label="Configured limit" value={n(burst.limit)} sub="tokens per window" />}
        {throttled && (
          <Stat
            label="Throttled after"
            value={`${n(s.tokens_before_first_429)} tokens`}
            sub={`${n(s.requests_before_first_429)} requests · ${n(s.seconds_to_first_429, 1)}s in`}
          />
        )}
        {throttled && (
          <Stat
            label="Got through after 429"
            value={n(s.successes_after_first_429)}
            sub="should be 0 within one window"
          />
        )}
        <Stat
          label="Latency p50 / p99"
          value={`${n(s.p50_latency_ms)} / ${n(s.p99_latency_ms)} ms`}
          sub={`p95 ${n(s.p95_latency_ms)} ms`}
        />
        <Stat
          label="Throughput"
          value={`${n(s.throughput_rps, 1)} req/s`}
          sub={`${n(s.token_throughput_per_sec, 1)} tokens/s`}
        />
        {retries > 0 && (
          <Stat label="Hidden retries" value={n(retries)} sub="extra HTTP attempts by the SDK" />
        )}
        {s.peak_concurrency !== undefined && s.peak_concurrency > 1 && (
          <Stat
            label="Peak concurrency"
            value={n(s.peak_concurrency)}
            sub={
              s.required_tokens_per_s
                ? `ramped up to reach ${n(s.required_tokens_per_s)} tokens/s`
                : 'requests in flight at once'
            }
          />
        )}
      </div>
      <ErrorSamples s={s} />
      <RetriedAttempts s={s} />
      <TrafficChart timeline={burst.timeline} limit={burst.limit ?? null} />
    </section>
  );
}

/** Several bursts drawn on one timeline (a chart_group) — e.g. before and
 * after waiting out a rate-limit window, or user A then user B. */
export function TrafficGroupPanel({ bursts }: { bursts: TrafficBurst[] }) {
  const [showChart, setShowChart] = useState(bursts.some((b) => b.chart));
  const sorted = [...bursts].sort((a, b) => (a.t0 ?? 0) - (b.t0 ?? 0));
  const start = sorted[0]?.t0 ?? 0;
  const segments = sorted.map((b) => ({
    label: b.label ?? formatTaskName(b.task),
    offset: Math.max(0, (b.t0 ?? start) - start),
    timeline: b.timeline,
  }));
  const limit = sorted.find((b) => b.limit != null)?.limit ?? null;
  return (
    <section className="maaspal-panel" aria-label="Traffic">
      <div className="maaspal-panel__header">
        <p className="maaspal-panel__title">Traffic</p>
        <Button variant="link" isInline onClick={() => setShowChart((v) => !v)} aria-expanded={showChart}>
          {showChart ? 'Hide chart' : 'Show chart'}
        </Button>
      </div>
      {sorted.map((b) => (
        <TrafficLine key={b.result_key} burst={b} bare />
      ))}
      {showChart && (
        <div style={{ marginTop: '0.6rem' }}>
          <TrafficChart segments={segments} limit={limit} />
        </div>
      )}
    </section>
  );
}

function StepChart({
  title,
  unit,
  stages,
  value,
}: {
  title: string;
  unit: string;
  stages: LoadStage[];
  value: (st: LoadStage) => number;
}) {
  const W = 300;
  const H = 140;
  const M = { top: 20, right: 12, bottom: 28, left: 46 };
  const values = stages.map(value);
  const yMax = Math.max(...values, 1) * 1.15;
  const sx = (i: number) => M.left + (stages.length === 1 ? 0.5 : i / (stages.length - 1)) * (W - M.left - M.right);
  const sy = (v: number) => H - M.bottom - (v / yMax) * (H - M.top - M.bottom);
  const d = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');
  return (
    <figure style={{ margin: 0 }}>
      <figcaption style={{ fontSize: '0.8rem', fontWeight: 600, color: COLOR.text }}>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${title} by concurrency step`}>
        <line x1={M.left} x2={W - M.right} y1={sy(0)} y2={sy(0)} stroke={COLOR.border} />
        <text x={M.left - 6} y={sy(yMax / 1.15)} dy="0.32em" textAnchor="end" fontSize={11} fill={COLOR.chart.axis}>
          {n(yMax / 1.15)}
        </text>
        <text x={M.left - 6} y={10} textAnchor="end" fontSize={11} fill={COLOR.chart.axis}>
          {unit}
        </text>
        <path d={d} fill="none" stroke={COLOR.chart.series} strokeWidth={2} />
        {stages.map((st, i) => (
          <g key={st.concurrency}>
            <circle cx={sx(i)} cy={sy(values[i])} r={4} fill={COLOR.chart.series} stroke={COLOR.surface} strokeWidth={2}>
              <title>
                {st.concurrency} in flight: {n(values[i], values[i] < 10 ? 1 : 0)} {unit}
              </title>
            </circle>
            <text x={sx(i)} y={sy(values[i]) - 8} textAnchor="middle" fontSize={11} fill={COLOR.text}>
              {n(values[i], values[i] < 10 ? 1 : 0)}
            </text>
            <text x={sx(i)} y={H - M.bottom + 16} textAnchor="middle" fontSize={11} fill={COLOR.chart.axis}>
              {st.concurrency}
            </text>
          </g>
        ))}
      </svg>
      <p className="maaspal-stat__sub" style={{ margin: 0, textAlign: 'center' }}>
        requests in flight
      </p>
    </figure>
  );
}

/** Step load: one row per concurrency step, plus throughput and p95 by step
 * — where MaaS starts to struggle, not one averaged number. */
function StagesPanel({ burst }: { burst: TrafficBurst }) {
  const s = burst.summary;
  const stages = s.stages ?? [];
  const showAttempts = stages.some((st) => (st.failed_attempts_pct ?? 0) > 0);
  return (
    <section className="maaspal-panel" aria-label="Load steps">
      <p className="maaspal-panel__title">Load by step</p>
      <div className="maaspal-table-scroll">
        <table className="maaspal-detail-table">
          <thead>
            <tr>
              <th>In flight</th>
              <th>Requests</th>
              <th>Req/s</th>
              <th>Tokens/s</th>
              <th>p50</th>
              <th>p95</th>
              <th>p99</th>
              <th>Errors</th>
              <th>Throttled</th>
              {showAttempts && (
                <th title="Share of all HTTP attempts that failed, including ones the SDK retried — the gateway can start failing before callers notice">
                  Failed attempts
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {stages.map((st) => (
              <tr key={st.concurrency}>
                <td>{st.concurrency}</td>
                <td>{n(st.requests)}</td>
                <td>{n(st.requests_per_s, 1)}</td>
                <td>{n(st.tokens_per_s)}</td>
                <td>{n(st.p50_latency_ms)} ms</td>
                <td>{n(st.p95_latency_ms)} ms</td>
                <td>{n(st.p99_latency_ms)} ms</td>
                <td>{st.error_rate_pct}%</td>
                <td>{st.throttled_pct}%</td>
                {showAttempts && <td>{st.failed_attempts_pct ?? 0}%</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ErrorSamples s={s} />
      <RetriedAttempts s={s} />
      <div className="maaspal-chart-grid" style={{ marginTop: '0.75rem' }}>
        <StepChart title="Throughput" unit="req/s" stages={stages} value={(st) => st.requests_per_s} />
        <StepChart title="p95 latency" unit="ms" stages={stages} value={(st) => st.p95_latency_ms} />
      </div>
      {/* The same tokens-over-time chart the panel showed while running,
          so it doesn't disappear once the step table arrives. */}
      <TrafficChart timeline={burst.timeline} limit={burst.limit ?? null} />
    </section>
  );
}

/** One line for a burst whose shape over time doesn't answer the scenario's
 * question (e.g. "3 requests · 3 denied") — the numbers, without a chart. */
function TrafficLine({
  burst,
  bare = false,
  onToggle,
}: {
  burst: TrafficBurst;
  bare?: boolean;
  onToggle?: () => void;
}) {
  const s = burst.summary;
  const heading = burst.label ? `${formatTaskName(burst.task)} — ${burst.label}` : formatTaskName(burst.task);
  const firstError = !s.success_count ? s.error_samples?.[0] : undefined;
  return (
    <div className={bare ? 'maaspal-traffic-line maaspal-traffic-line--bare' : 'maaspal-traffic-line'} aria-label={`Traffic: ${heading}`}>
      <span className="maaspal-traffic-line__name">{heading}</span>
      <span>
        {n(s.total_requests)} requests · {outcomeBreakdown(s)} · {n(s.total_tokens_sent)} tokens · p50{' '}
        {n(s.p50_latency_ms)} ms
        {s.tokens_before_first_429 !== undefined && ` · throttled after ${n(s.tokens_before_first_429)} tokens`}
      </span>
      {onToggle && burst.timeline.length > 0 && (
        <Button variant="link" isInline onClick={onToggle} aria-expanded={false} className="maaspal-traffic-line__toggle">
          Show chart
        </Button>
      )}
      {firstError && (
        <span className="maaspal-traffic-line__error">
          most common error: {n(firstError.count)}× <code>{firstError.message}</code>
        </span>
      )}
    </div>
  );
}


function ResourceItem({ r }: { r: RunResource }) {
  const st = RESOURCE_STATUS[r.status ?? 'active'] ?? RESOURCE_STATUS.active;
  return (
    <li className="maaspal-steps__resource">
      <span className="maaspal-resource-list__kind">
        {r.action === 'patched' ? `${r.kind} (changed)` : r.kind}
      </span>
      <code>{r.name}</code>
      <span className="maaspal-steps__status" style={{ color: toneColor(st.tone) }}>
        {st.label}
      </span>
      {r.subscription && <span className="maaspal-steps__meta"> · on {r.subscription}</span>}
      {r.owner && <span className="maaspal-steps__meta"> · as {r.owner}</span>}
    </li>
  );
}


function cleanupSummary(resources: RunResource[], cleanupStatus?: string): { text: string; color: string } {
  const count = (status: string) => resources.filter((r) => r.status === status).length;
  const failed = count('cleanup failed');
  const left = count('left in place');
  const done = count('removed') + count('restored') + count('revoked');
  if (failed) return { text: `${failed} of ${resources.length} could not be removed — use Clean Up Now, or remove them by hand.`, color: toneColor('danger') };
  if (left) return { text: `${left} left in place (auto cleanup was off) — use Clean Up Now to remove them.`, color: toneColor('warning') };
  if (done === resources.length) return { text: `Everything this run created was removed or put back.`, color: toneColor('success') };
  if (cleanupStatus === 'cleaning') return { text: 'Cleaning up…', color: toneColor('info') };
  return { text: 'Removed automatically when the run finishes.', color: COLOR.muted };
}

/** What happened, step by step: each task's one-line narration plus
 * whatever that step created, with each object's own cleanup status, and a
 * final Cleanup row. The chips above show live progress; this is the story. */
// Up to this many objects of one kind are listed one by one; more collapse
// into a count-per-status row (a run can create thousands of API keys).
const LIST_INDIVIDUALLY = 5;
const LIST_MAX_RENDERED = 200;

/** One step's created objects: listed individually when few, otherwise one
 * summary row per kind with counts by status and a "Show all" toggle. */
function StepResources({ resources }: { resources: RunResource[] }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const byKind = new Map<string, RunResource[]>();
  for (const r of resources) byKind.set(r.kind, [...(byKind.get(r.kind) ?? []), r]);
  return (
    <ul className="maaspal-steps__resources">
      {[...byKind.entries()].map(([kind, items]) => {
        if (items.length <= LIST_INDIVIDUALLY) {
          return items.map((r) => <ResourceItem key={`${r.kind}-${r.name}`} r={r} />);
        }
        const counts = new Map<string, number>();
        for (const r of items) counts.set(r.status ?? 'active', (counts.get(r.status ?? 'active') ?? 0) + 1);
        const isOpen = !!expanded[kind];
        return (
          <li key={kind} className="maaspal-steps__resource">
            <span className="maaspal-resource-list__kind">
              {kind} × {items.length.toLocaleString()}
            </span>
            {[...counts.entries()].map(([status, count]) => {
              const st = RESOURCE_STATUS[status] ?? RESOURCE_STATUS.active;
              return (
                <span key={status} className="maaspal-steps__status" style={{ color: toneColor(st.tone) }}>
                  {count.toLocaleString()} {st.label}
                </span>
              );
            })}
            <button
              type="button"
              className="maaspal-assertion-card__details-toggle"
              style={{ marginLeft: '0.6rem' }}
              aria-expanded={isOpen}
              onClick={() => setExpanded((prev) => ({ ...prev, [kind]: !isOpen }))}
            >
              {isOpen ? 'Hide' : 'Show all'}
            </button>
            {isOpen && (
              <ul className="maaspal-steps__resources">
                {items.slice(0, LIST_MAX_RENDERED).map((r) => (
                  <ResourceItem key={`${r.kind}-${r.name}`} r={r} />
                ))}
                {items.length > LIST_MAX_RENDERED && (
                  <li className="maaspal-steps__meta">
                    …and {(items.length - LIST_MAX_RENDERED).toLocaleString()} more
                  </li>
                )}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function RunSteps({
  tasks,
  resources,
  cleanupStatus,
}: {
  tasks: TaskProgressEntry[];
  resources: RunResource[];
  cleanupStatus?: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  if (!tasks.some((t) => t.summary) && resources.length === 0) return null;
  const byTask = new Map<string, RunResource[]>();
  for (const r of resources) {
    const key = r.task ?? '';
    byTask.set(key, [...(byTask.get(key) ?? []), r]);
  }
  const unattributed = byTask.get('') ?? [];
  const cleanup = cleanupSummary(resources, cleanupStatus);
  return (
    <section className="maaspal-panel" aria-label="What happened">
      <div className="maaspal-panel__header">
        <p className="maaspal-panel__title">What happened</p>
        <Button variant="link" isInline onClick={() => setCollapsed((c) => !c)} aria-expanded={!collapsed}>
          {collapsed ? 'Show steps' : 'Collapse'}
        </Button>
      </div>
      {collapsed ? (
        <p className="maaspal-steps__text" style={{ margin: 0 }}>
          {tasks.filter((t) => t.status === 'DONE').length} of {tasks.length} steps done
          {resources.length > 0 && ` · ${cleanup.text}`}
        </p>
      ) : (
      <ol className="maaspal-steps">
        {tasks.map((t) => {
          const own = byTask.get(t.name) ?? [];
          const step = statusStyle(t.status);
          const icon = { icon: step.icon, color: toneColor(step.tone) };
          return (
            <li key={t.name} className={`maaspal-steps__item maaspal-steps__item--${t.status.toLowerCase()}`}>
              <span className="maaspal-steps__icon" style={{ color: icon.color }} aria-label={t.status}>
                {icon.icon}
              </span>
              <div>
                <span className="maaspal-steps__task">{formatTaskName(t.name)}</span>
                {t.summary ? (
                  <span className="maaspal-steps__text">{t.summary}</span>
                ) : (
                  t.status === 'PENDING' && <span className="maaspal-steps__text maaspal-steps__text--muted">waiting</span>
                )}
                {own.length > 0 && <StepResources resources={own} />}
              </div>
            </li>
          );
        })}
        {resources.length > 0 && (
          <li className="maaspal-steps__item">
            <span className="maaspal-steps__icon" style={{ color: cleanup.color }}>
              ⟲
            </span>
            <div>
              <span className="maaspal-steps__task">Cleanup</span>
              <span className="maaspal-steps__text" style={{ color: cleanup.color }}>
                {cleanup.text}
              </span>
              {unattributed.length > 0 && <StepResources resources={unattributed} />}
            </div>
          </li>
        )}
      </ol>
      )}
    </section>
  );
}

/** A run's conclusion when the scenario finds something out rather than
 * checking an expectation (e.g. "Limits are per user"). */
export function FindingsPanel({ findings }: { findings: RunFinding[] }) {
  if (findings.length === 0) return null;
  return (
    <>
      {findings.map((f, i) => (
        <section key={i} className="maaspal-finding" aria-label={`Finding: ${f.title}`}>
          <p className="maaspal-finding__label">Finding</p>
          <p className="maaspal-finding__title">{f.title}</p>
          <p className="maaspal-finding__text">{f.text}</p>
        </section>
      ))}
    </>
  );
}

/** MaaS-reported vs harness-counted, side by side (one chart per measure). */
export function MetricsChartsPanel({ charts }: { charts: MetricsChart[] }) {
  if (charts.length === 0) return null;
  return (
    <section className="maaspal-panel" aria-label="MaaS metrics vs this run">
      <p className="maaspal-panel__title">MaaS metrics vs what this run sent</p>
      <p className="maaspal-stat__sub" style={{ margin: '-0.3rem 0 0.6rem' }}>
        MaaS&apos;s counters lag behind real traffic until Prometheus scrapes them (~30 s); the lines
        should meet once it catches up.
      </p>
      <div className="maaspal-chart-grid">
        {charts.map((c) => (
          <MetricsComparisonChart key={c.title} chart={c} />
        ))}
      </div>
    </section>
  );
}

/** Per-row detail a task published (one row per model checked, etc.). */
export function DetailTable({ table }: { table: RunTable }) {
  return (
    <section className="maaspal-panel" aria-label={table.title}>
      <p className="maaspal-panel__title">{table.title}</p>
      <div className="maaspal-table-scroll">
        <table className="maaspal-detail-table">
          <thead>
            <tr>
              {table.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
