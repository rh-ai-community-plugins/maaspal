import type {
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

const VERDICT_STYLE: Record<RunVerdict['status'], { color: string; icon: string; label: string }> = {
  PASS: { color: '#2e7d32', icon: '✓', label: 'Behaving as expected' },
  FAIL: { color: '#c62828', icon: '✗', label: 'Not behaving as expected' },
  CANCELLED: { color: '#b26a00', icon: '⊘', label: 'Stopped' },
};

/** One plain-language sentence summing the run up, written by the harness
 * at the end of the run (harness/runner.py:_render_verdict). */
export function VerdictBanner({ verdict }: { verdict: RunVerdict }) {
  const style = VERDICT_STYLE[verdict.status] ?? VERDICT_STYLE.FAIL;
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
  if (s.server_error_count) parts.push(`${n(s.server_error_count)} 5xx`);
  if (s.other_error_count) parts.push(`${n(s.other_error_count)} other`);
  return parts.join(' · ');
}

/** The numbers a user needs to judge a burst themselves — the same values the
 * assertions check, plus context (tokens split, latency spread, retries). */
export function TrafficPanel({ burst }: { burst: TrafficBurst }) {
  const s = burst.summary;
  if (!burst.chart) return <TrafficLine burst={burst} />;
  const retries =
    s.http_attempts !== undefined && s.total_requests !== undefined && s.http_attempts > s.total_requests
      ? s.http_attempts - s.total_requests
      : 0;
  const throttled = s.tokens_before_first_429 !== undefined;
  const heading = burst.label ? `${formatTaskName(burst.task)} — ${burst.label}` : formatTaskName(burst.task);
  return (
    <section className="maaspal-panel" aria-label={`Traffic: ${heading}`}>
      <p className="maaspal-panel__title">Traffic · {heading}</p>
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
      </div>
      <TrafficChart timeline={burst.timeline} limit={burst.limit ?? null} />
    </section>
  );
}

/** One line for a burst whose shape over time doesn't answer the scenario's
 * question (e.g. "3 requests · 3 denied") — the numbers, without a chart. */
function TrafficLine({ burst }: { burst: TrafficBurst }) {
  const s = burst.summary;
  const heading = burst.label ? `${formatTaskName(burst.task)} — ${burst.label}` : formatTaskName(burst.task);
  return (
    <div className="maaspal-traffic-line" aria-label={`Traffic: ${heading}`}>
      <span className="maaspal-traffic-line__name">{heading}</span>
      <span>
        {n(s.total_requests)} requests · {outcomeBreakdown(s)} · {n(s.total_tokens_sent)} tokens · p50{' '}
        {n(s.p50_latency_ms)} ms
      </span>
    </div>
  );
}

const RESOURCE_STATUS: Record<string, { label: string; color: string }> = {
  active: { label: 'exists', color: '#6a6e73' },
  removed: { label: 'removed ✓', color: '#2e7d32' },
  restored: { label: 'restored ✓', color: '#2e7d32' },
  revoked: { label: 'revoked ✓', color: '#2e7d32' },
  'cleanup failed': { label: 'cleanup failed ✗', color: '#c62828' },
  'left in place': { label: 'left in place', color: '#b26a00' },
};

function ResourceItem({ r }: { r: RunResource }) {
  const st = RESOURCE_STATUS[r.status ?? 'active'] ?? RESOURCE_STATUS.active;
  return (
    <li className="maaspal-steps__resource">
      <span className="maaspal-resource-list__kind">
        {r.action === 'patched' ? `${r.kind} (changed)` : r.kind}
      </span>
      <code>{r.name}</code>
      <span className="maaspal-steps__status" style={{ color: st.color }}>
        {st.label}
      </span>
      {r.subscription && <span className="maaspal-steps__meta"> · on {r.subscription}</span>}
      {r.owner && <span className="maaspal-steps__meta"> · as {r.owner}</span>}
    </li>
  );
}

const STEP_ICON: Record<TaskProgressEntry['status'], { icon: string; color: string }> = {
  PENDING: { icon: '○', color: '#9e9e9e' },
  RUNNING: { icon: '◎', color: '#1565c0' },
  DONE: { icon: '✓', color: '#2e7d32' },
  FAIL: { icon: '✗', color: '#c62828' },
  CANCELLED: { icon: '⊘', color: '#b26a00' },
};

function cleanupSummary(resources: RunResource[], cleanupStatus?: string): { text: string; color: string } {
  const count = (status: string) => resources.filter((r) => r.status === status).length;
  const failed = count('cleanup failed');
  const left = count('left in place');
  const done = count('removed') + count('restored') + count('revoked');
  if (failed) return { text: `${failed} of ${resources.length} could not be removed — use Clean Up Now, or remove them by hand.`, color: '#c62828' };
  if (left) return { text: `${left} left in place (auto cleanup was off) — use Clean Up Now to remove them.`, color: '#b26a00' };
  if (done === resources.length) return { text: `Everything this run created was removed or put back.`, color: '#2e7d32' };
  if (cleanupStatus === 'cleaning') return { text: 'Cleaning up…', color: '#1565c0' };
  return { text: 'Removed automatically when the run finishes.', color: '#6a6e73' };
}

/** What happened, step by step: each task's one-line narration plus
 * whatever that step created, with each object's own cleanup status, and a
 * final Cleanup row. The chips above show live progress; this is the story. */
export function RunSteps({
  tasks,
  resources,
  cleanupStatus,
}: {
  tasks: TaskProgressEntry[];
  resources: RunResource[];
  cleanupStatus?: string;
}) {
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
      <p className="maaspal-panel__title">What happened</p>
      <ol className="maaspal-steps">
        {tasks.map((t) => {
          const own = byTask.get(t.name) ?? [];
          const icon = STEP_ICON[t.status];
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
                {own.length > 0 && (
                  <ul className="maaspal-steps__resources">
                    {own.map((r) => (
                      <ResourceItem key={`${r.kind}-${r.name}`} r={r} />
                    ))}
                  </ul>
                )}
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
              {unattributed.length > 0 && (
                <ul className="maaspal-steps__resources">
                  {unattributed.map((r) => (
                    <ResourceItem key={`${r.kind}-${r.name}`} r={r} />
                  ))}
                </ul>
              )}
            </div>
          </li>
        )}
      </ol>
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
