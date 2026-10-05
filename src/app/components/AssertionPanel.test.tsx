import { render, screen } from '@testing-library/react';
import { AssertionPanel } from './AssertionPanel';
import type { AssertionState, TaskProgressEntry } from '../api/client';

const noProgress: TaskProgressEntry[] = [];

test('shows placeholder when assertions list is empty', () => {
  render(<AssertionPanel assertions={[]} taskProgress={noProgress} />);
  expect(screen.getByText(/waiting for assertion data/i)).toBeInTheDocument();
});

test('renders grouped assertion cards with task section headers', () => {
  const taskProgress: TaskProgressEntry[] = [
    { name: 'send_requests', status: 'DONE' },
  ];
  const assertions: AssertionState[] = [
    { task: 'send_requests', name: 'error_rate_pct', status: 'PASSING', value: 2.1, expression: '< 5' },
    { task: 'send_requests', name: 'p99_latency_ms', status: 'PENDING', value: null },
    { task: 'send_requests', name: 'throughput_rps', status: 'FAILING', value: 15 },
  ];

  render(<AssertionPanel assertions={assertions} taskProgress={taskProgress} />);

  expect(screen.getByText(/send requests/i)).toBeInTheDocument();
  expect(screen.getByText(/Error Rate/i)).toBeInTheDocument();
  expect(screen.getByText('PASSING')).toBeInTheDocument();
  expect(screen.getByText('PENDING')).toBeInTheDocument();
  expect(screen.getByText('FAILING')).toBeInTheDocument();
});

test('renders global assertions in their own section', () => {
  const assertions: AssertionState[] = [
    { task: null, name: 'error_rate_pct', status: 'PASSING', value: 1.0, expression: '< 5' },
  ];

  render(<AssertionPanel assertions={assertions} taskProgress={noProgress} />);

  expect(screen.getByText(/global/i)).toBeInTheDocument();
  expect(screen.getByText('PASSING')).toBeInTheDocument();
});
