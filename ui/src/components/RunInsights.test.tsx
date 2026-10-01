import { fireEvent, render, screen } from '@testing-library/react';
import {
  DetailTable,
  FindingsPanel,
  MetricsChartsPanel,
  RunSteps,
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
  expect(screen.getAllByText('Configured limit')).toHaveLength(2); // stat + chart legend
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
