import { fireEvent, render, screen } from '@testing-library/react';
import { DetailTable, ResourcesPanel, TrafficPanel, VerdictBanner } from './RunInsights';
import type { TrafficBurst } from '../api/client';

const burst: TrafficBurst = {
  task: 'send_requests',
  result_key: 'inference_results',
  planned: 60,
  limit: 100,
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

test('resources panel lists what the run created and the cleanup state', () => {
  render(
    <ResourcesPanel
      resources={[
        { kind: 'MaaSSubscription', name: 'models-as-a-service/maaspal-rate-limit-test', action: 'created' },
        { kind: 'API key', name: 'maaspal-rate-key', subscription: 'maaspal-rate-limit-test', action: 'created' },
      ]}
      cleanupStatus="skipped"
    />,
  );
  expect(screen.getByText('models-as-a-service/maaspal-rate-limit-test')).toBeInTheDocument();
  expect(screen.getByText(/on maaspal-rate-limit-test/)).toBeInTheDocument();
  expect(screen.getByText(/left in place/i)).toBeInTheDocument();
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
