import type { RunResource, RunTable, RunVerdict, TrafficBurst, TrafficSummary } from '../api/client';
import { formatTaskName } from '../scenarioTitles';
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

/** "This run created": what the run put on (or changed in) the cluster, by
 * name, and whether cleanup has removed it yet. */
export function ResourcesPanel({ resources, cleanupStatus }: { resources: RunResource[]; cleanupStatus?: string }) {
  if (resources.length === 0) return null;
  const cleanupNote =
    cleanupStatus === 'done'
      ? 'All cleaned up ✓'
      : cleanupStatus === 'cleaning'
        ? 'Cleaning up…'
        : cleanupStatus === 'skipped'
          ? 'Left in place — auto cleanup was off'
          : cleanupStatus === 'failed'
            ? 'Cleanup failed — some of these may remain'
            : 'Removed automatically when the run finishes';
  return (
    <section className="maaspal-panel" aria-label="Resources this run created">
      <p className="maaspal-panel__title">This run created</p>
      <ul className="maaspal-resource-list">
        {resources.map((r, i) => (
          <li key={`${r.kind}-${r.name}-${i}`}>
            <span className="maaspal-resource-list__kind">{r.kind}</span>
            <code>{r.name}</code>
            {r.subscription && <span style={{ color: '#777' }}> · on {r.subscription}</span>}
            {r.owner && <span style={{ color: '#777' }}> · as {r.owner}</span>}
            {r.action && r.action !== 'created' && <span style={{ color: '#b26a00' }}> · {r.action}</span>}
          </li>
        ))}
      </ul>
      <p className="maaspal-stat__sub" style={{ marginTop: '0.5rem' }}>{cleanupNote}</p>
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
