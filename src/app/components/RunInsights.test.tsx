import { fireEvent, render, screen } from '@testing-library/react';
import {
  DetailTable,
  FindingsPanel,
  MetricsChartsPanel,
  RunSteps,
  TrafficGroupPanel,
  TrafficPanel,
  VerdictBanner,
} from './RunInsights';
import type { TrafficBurst } from '../api/client';

const burst: TrafficBurst = {
  task: 'send_requests',
  result_key: 'inference_results',
  planned: 60,
  limit: 100,
  chart: true,
  summary: {
    total_requests: 9,
    http_attempts: 9,
    success_count: 4,
    rate_limited_count: 5,
    total_tokens_sent: 112,
    prompt_tokens_sent: 40,
    completion_tokens_sent: 72,
    tokens_before_first_429: 112,
    requests_before_first_429: 4,
    seconds_to_first_429: 2.4,
    successes_after_first_429: 0,
    p50_latency_ms: 120,
    p95_latency_ms: 180,
    p99_latency_ms: 200,
    throughput_rps: 1.5,
    token_throughput_per_sec: 40,
  },
  timeline: [
    [0.5, 28, 'ok', 120],
    [1.0, 56, 'ok', 110],
    [1.6, 84, 'ok', 130],
    [2.2, 112, 'ok', 125],
    [2.4, 112, 'throttled', 15],
  ],
};

test('verdict banner states the outcome in plain language', () => {
  render(
    <VerdictBanner
      verdict={{ status: 'PASS', text: 'MaaS throttled you after 112 tokens.', checks_passed: 4, checks_total: 4 }}
    />,
  );
  expect(screen.getByText('Behaving as expected.')).toBeInTheDocument();
  expect(screen.getByText(/throttled you after 112 tokens/)).toBeInTheDocument();
  expect(screen.getByText('4 of 4 checks passed')).toBeInTheDocument();
});

test('traffic panel shows where throttling started against the configured limit', () => {
  render(<TrafficPanel burst={burst} />);
  expect(screen.getByText('112 tokens')).toBeInTheDocument();
  expect(screen.getByText('4 requests · 2.4s in')).toBeInTheDocument();
  expect(screen.getByText('Configured limit')).toBeInTheDocument(); // stat
  expect(screen.getByText('Configured limit (100)')).toBeInTheDocument(); // chart legend
  expect(screen.getByText('4 OK · 5 throttled')).toBeInTheDocument();
  // Chart legend names the non-OK outcome present, never colour alone.
  expect(screen.getByText('Throttled (429)')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: /tokens served over time/i })).toBeInTheDocument();
});

test('traffic panel surfaces hidden SDK retries', () => {
  render(<TrafficPanel burst={{ ...burst, summary: { ...burst.summary, http_attempts: 13 } }} />);
  expect(screen.getByText('Hidden retries')).toBeInTheDocument();
  expect(screen.getByText('4')).toBeInTheDocument();
});

test('chart data is reachable as a table', () => {
  render(<TrafficPanel burst={burst} />);
  fireEvent.click(screen.getByRole('button', { name: /show data table/i }));
  expect(screen.getAllByRole('row')).toHaveLength(burst.timeline.length + 1);
});

test('a burst without a chart is a single summary line', () => {
  render(
    <TrafficPanel
      burst={{
        task: 'verify_revoked_key_denied',
        result_key: 'r',
        chart: false,
        summary: { total_requests: 3, success_count: 0, unauthorized_count: 3, total_tokens_sent: 0, p50_latency_ms: 12 },
        timeline: [[0.1, 0, 'denied', 12]],
      }}
    />,
  );
  expect(screen.getByText(/3 requests · 0 OK · 3 denied/)).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

test('steps show each step with the resources it created and their cleanup status', () => {
  render(
    <RunSteps
      tasks={[
        { name: 'apply_rate_limit_subscription', status: 'DONE', summary: 'Created subscription ns/test' },
        { name: 'provision_api_key', status: 'DONE', summary: 'Created 1 API key' },
        { name: 'send_requests', status: 'RUNNING', summary: '5 requests · 5 OK' },
      ]}
      resources={[
        { kind: 'MaaSSubscription', name: 'ns/test', task: 'apply_rate_limit_subscription', status: 'removed' },
        { kind: 'MaaSSubscription', name: 'ns/existing', task: 'apply_rate_limit_subscription', action: 'patched', status: 'restored' },
        { kind: 'API key', name: 'maaspal-rate-key', task: 'provision_api_key', subscription: 'test', status: 'cleanup failed' },
      ]}
      cleanupStatus="failed"
    />,
  );
  expect(screen.getByText('Created subscription ns/test')).toBeInTheDocument();
  expect(screen.getByText('removed ✓')).toBeInTheDocument();
  expect(screen.getByText('restored ✓')).toBeInTheDocument();
  expect(screen.getByText('MaaSSubscription (changed)')).toBeInTheDocument();
  expect(screen.getByText('cleanup failed ✗')).toBeInTheDocument();
  expect(screen.getByText(/1 of 3 could not be removed/)).toBeInTheDocument();
});

test('steps say when resources were left in place', () => {
  render(
    <RunSteps
      tasks={[{ name: 'provision_api_key', status: 'DONE', summary: 'Created 1 API key' }]}
      resources={[{ kind: 'API key', name: 'k', task: 'provision_api_key', status: 'left in place' }]}
      cleanupStatus="skipped"
    />,
  );
  expect(screen.getByText(/left in place \(auto cleanup was off\)/)).toBeInTheDocument();
});

test('findings state what the run found out', () => {
  render(<FindingsPanel findings={[{ title: 'Limits are per user', text: 'Each user gets their own budget.' }]} />);
  expect(screen.getByText('Finding')).toBeInTheDocument();
  expect(screen.getByText('Limits are per user')).toBeInTheDocument();
});

test('metrics charts compare MaaS-reported values with what was sent', () => {
  render(
    <MetricsChartsPanel
      charts={[
        {
          title: 'Tokens',
          unit: 'tokens',
          maas_label: 'Reported by MaaS',
          harness_label: 'Sent by this run',
          points: [[0, 0, 0], [5, 120, 900], [35, 900, 900]],
        },
      ]}
    />,
  );
  expect(screen.getByRole('img', { name: /tokens: reported by maas 900, sent by this run 900/i })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /show data table/i }));
  expect(screen.getAllByRole('row')).toHaveLength(4);
});

test('detail table renders the rows a task published', () => {
  render(
    <DetailTable
      table={{ title: 'Model health', columns: ['Model', 'Ready'], rows: [['llm/a', '✓'], ['llm/b', '✗']] }}
    />,
  );
  expect(screen.getByText('Model health')).toBeInTheDocument();
  expect(screen.getByText('llm/b')).toBeInTheDocument();
});


test('many objects of one kind collapse into a count per status, with Show all', () => {
  const keys = Array.from({ length: 1000 }, (_, i) => ({
    kind: 'API key',
    name: `key-${i}`,
    task: 'provision_api_key',
    status: (i < 998 ? 'removed' : 'cleanup failed') as 'removed' | 'cleanup failed',
  }));
  render(
    <RunSteps
      tasks={[{ name: 'provision_api_key', status: 'DONE', summary: 'Created 1000 API keys' }]}
      resources={keys}
      cleanupStatus="failed"
    />,
  );
  expect(screen.getByText('API key × 1,000')).toBeInTheDocument();
  expect(screen.getByText('998 removed ✓')).toBeInTheDocument();
  expect(screen.getByText('2 cleanup failed ✗')).toBeInTheDocument();
  expect(screen.queryByText('key-0')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  expect(screen.getByText('key-0')).toBeInTheDocument();
  expect(screen.getByText('…and 800 more')).toBeInTheDocument();
});

test('the steps panel can be collapsed to a one-line summary', () => {
  render(
    <RunSteps
      tasks={[{ name: 'provision_api_key', status: 'DONE', summary: 'Created 1 API key' }]}
      resources={[{ kind: 'API key', name: 'k', task: 'provision_api_key', status: 'removed' }]}
      cleanupStatus="done"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Collapse' }));
  expect(screen.queryByText('Created 1 API key')).not.toBeInTheDocument();
  expect(screen.getByText(/1 of 1 steps done/)).toBeInTheDocument();
});

test('bursts in one chart group share a timeline with the wait between them shaded', () => {
  const base = { task: 'send_requests', chart: true, chart_group: 'recovery', limit: 50 };
  render(
    <TrafficGroupPanel
      bursts={[
        {
          ...base, result_key: 'a', label: 'Before the wait', t0: 1000,
          summary: { total_requests: 4, success_count: 2, rate_limited_count: 2, total_tokens_sent: 60, tokens_before_first_429: 60 },
          timeline: [[0.2, 30, 'ok', 100], [0.4, 60, 'ok', 100], [0.5, 60, 'throttled', 5], [0.6, 60, 'throttled', 5]],
        },
        {
          ...base, result_key: 'b', label: 'After the wait', t0: 1066,
          summary: { total_requests: 1, success_count: 1, total_tokens_sent: 30 },
          timeline: [[0.2, 30, 'ok', 100]],
        },
      ]}
    />,
  );
  expect(screen.getAllByRole('img')).toHaveLength(1);
  expect(screen.getByRole('img', { name: /in 2 bursts/ })).toBeInTheDocument();
  expect(screen.getByText(/waiting 65s/)).toBeInTheDocument();
  expect(screen.getByText(/throttled after 60 tokens/)).toBeInTheDocument();
});

test('a burst that failed says why', () => {
  render(
    <TrafficPanel
      burst={{
        task: 'send_requests', result_key: 'd', chart: false,
        summary: {
          total_requests: 50, success_count: 0, not_found_count: 50, total_tokens_sent: 0,
          error_samples: [{ message: 'HTTP 404 Not Found', count: 50 }],
        },
        timeline: [],
      }}
    />,
  );
  expect(screen.getByText(/50 not found \(404\)/)).toBeInTheDocument();
  expect(screen.getByText('HTTP 404 Not Found')).toBeInTheDocument();
});

test('step load shows a row per step and charts throughput and p95 by step', () => {
  const stage = (c: number, rps: number, p95: number) => ({
    concurrency: c, requests: rps * 30, requests_per_s: rps, tokens_per_s: rps * 25,
    p50_latency_ms: p95 / 2, p95_latency_ms: p95, p99_latency_ms: p95 * 1.2, error_rate_pct: 0, throttled_pct: 0,
  });
  render(
    <TrafficPanel
      burst={{
        task: 'send_requests', result_key: 'l',
        summary: { total_requests: 900, success_count: 900, stages: [stage(5, 10, 200), stage(10, 18, 300), stage(25, 19, 900)] },
        timeline: [],
      }}
    />,
  );
  expect(screen.getByText('Load by step')).toBeInTheDocument();
  expect(screen.getAllByRole('row')).toHaveLength(4);
  expect(screen.getByRole('img', { name: 'Throughput by concurrency step' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'p95 latency by concurrency step' })).toBeInTheDocument();
});

test('step load keeps the tokens-over-time chart and shows what retried attempts got back', () => {
  render(
    <TrafficPanel
      burst={{
        task: 'send_requests', result_key: 'l', limit: null,
        summary: {
          total_requests: 100, http_attempts: 130, success_count: 99,
          error_samples: [{ message: 'HTTP 500 Internal Server Error', count: 1, median_ms: 202, p10_ms: 200, p90_ms: 210 }],
          failed_attempts: 31, retried_failed_attempts: 30,
          attempt_error_samples: [{ message: 'HTTP 500 Internal Server Error', count: 31, median_ms: 203 }],
          stages: [{
            concurrency: 5, requests: 100, requests_per_s: 3, tokens_per_s: 75, p50_latency_ms: 50,
            p95_latency_ms: 90, p99_latency_ms: 99, error_rate_pct: 1, throttled_pct: 0,
            http_attempts: 130, failed_attempts_pct: 23.85,
          }],
        },
        timeline: [[0.1, 25, 'ok', 50], [0.2, 50, 'error', 202]],
      }}
    />,
  );
  expect(screen.getByRole('img', { name: /2 requests over/ })).toBeInTheDocument();
  expect(screen.getByText(/The SDK retried 30 failed attempts/)).toBeInTheDocument();
  expect(screen.getByRole('list', { name: 'Failed attempts the SDK retried' })).toHaveTextContent('31×');
  expect(screen.getByText(/after 202 ms/)).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Failed attempts' })).toBeInTheDocument();
  expect(screen.getByText('23.85%')).toBeInTheDocument();
});


test('any burst can switch between its one-line summary and the full chart', () => {
  render(<TrafficPanel burst={{ ...burst, chart: false }} />);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Show chart' }));
  expect(screen.getByRole('img', { name: /tokens served over time/i })).toBeInTheDocument();
  expect(screen.getByText('Throttled after')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Show summary' }));
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

test('a grouped chart can be hidden and shown again', () => {
  const base = { task: 'send_requests', chart: true, chart_group: 'g', limit: 50 };
  render(
    <TrafficGroupPanel
      bursts={[
        { ...base, result_key: 'a', label: 'A', t0: 1, summary: { total_requests: 1, success_count: 1 }, timeline: [[0.1, 20, 'ok', 50]] },
        { ...base, result_key: 'b', label: 'B', t0: 5, summary: { total_requests: 1, success_count: 1 }, timeline: [[0.1, 20, 'ok', 50]] },
      ]}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Hide chart' }));
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show chart' }));
  expect(screen.getByRole('img')).toBeInTheDocument();
});

test('steps say where each burst of requests was sent from', () => {
  render(
    <RunSteps
      tasks={[
        { name: 'provision_api_key', status: 'DONE', summary: 'Created 1 API key' },
        { name: 'send_requests', status: 'FAIL', summary: '1 requests · 0 OK' },
        { name: 'verify_other_keys_still_work', status: 'DONE', summary: '3 requests · 3 OK' },
        { name: 'send_requests_after_window', status: 'DONE', summary: '3 requests · 3 OK' },
      ]}
      resources={[]}
      traffic={[
        { ...burst, task: 'send_requests', origin: 'browser' },
        { ...burst, task: 'verify_other_keys_still_work', origin: 'pod' },
        // A run from before the browser option: sent from the pod.
        { ...burst, task: 'send_requests_after_window', origin: undefined },
      ]}
    />,
  );
  expect(screen.getAllByText('Sent from the browser')).toHaveLength(1);
  expect(screen.getAllByText('Sent from the pod')).toHaveLength(2);
});

test('steps say which endpoint each step used and whether it streamed', () => {
  render(
    <RunSteps
      tasks={[
        { name: 'send_requests', status: 'DONE', summary: '3 requests · 3 OK' },
        { name: 'send_requests_after_window', status: 'DONE', summary: '3 requests · 3 OK' },
        { name: 'send_requests_each_type', status: 'DONE', summary: 'tried 3 types' },
      ]}
      resources={[]}
      traffic={[
        { ...burst, task: 'send_requests', request: '/v1/chat/completions', stream: true },
        // An older run: only the summary knows what was sent.
        { ...burst, task: 'send_requests_after_window', summary: { ...burst.summary, api: 'completions', stream: false } },
        { ...burst, task: 'send_requests_each_type', request: '/v1/chat/completions', stream: false },
        { ...burst, task: 'send_requests_each_type', request: '/v1/chat/completions', stream: true },
        { ...burst, task: 'send_requests_each_type', request: '/tokenize', stream: false },
      ]}
    />,
  );
  expect(screen.getByText('/v1/chat/completions · streamed')).toBeInTheDocument();
  expect(screen.getByText('/v1/completions · not streamed')).toBeInTheDocument();
  expect(screen.getByText('3 request types')).toBeInTheDocument();
});
