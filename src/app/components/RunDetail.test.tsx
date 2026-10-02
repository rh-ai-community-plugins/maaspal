import { act, render, screen, waitFor } from '@testing-library/react';
import { RunDetail } from './RunDetail';
import * as client from '../api/client';
import type { Run } from '../api/client';

jest.mock('../api/client');
// LogStream pulls in its own polling/scroll machinery unrelated to this
// feature — stub it so these tests focus on the metadata bar only.
jest.mock('./LogStream', () => ({ LogStream: () => null }));

const mockGetRun = client.getRun as jest.MockedFunction<typeof client.getRun>;
const mockGetAssertions = client.getAssertions as jest.MockedFunction<typeof client.getAssertions>;
const mockGetProgress = client.getProgress as jest.MockedFunction<typeof client.getProgress>;
const mockSetAutoCleanup = client.setAutoCleanup as jest.MockedFunction<typeof client.setAutoCleanup>;
const mockCleanupRun = client.cleanupRun as jest.MockedFunction<typeof client.cleanupRun>;

function baseRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    scenario: 'single_key_load',
    status: 'RUNNING',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    auto_cleanup: true,
    cleanup_status: 'pending',
    ...overrides,
  };
}

beforeEach(() => {
  mockGetAssertions.mockResolvedValue([]);
  mockGetProgress.mockResolvedValue({ tasks: [] });
});

afterEach(() => {
  jest.clearAllMocks();
});

test('auto cleanup switch is interactive while the run is active', async () => {
  mockGetRun.mockResolvedValue(baseRun({ status: 'RUNNING', auto_cleanup: true }));
  mockSetAutoCleanup.mockResolvedValue({ auto_cleanup: false });

  await act(async () => {
    render(<RunDetail runId="run-1" onBack={jest.fn()} />);
  });

  const toggle = await screen.findByLabelText('Auto cleanup');
  expect(toggle).not.toBeDisabled();
  expect((toggle as HTMLInputElement).checked).toBe(true);
});

test('auto cleanup switch is disabled once the run is terminal', async () => {
  mockGetRun.mockResolvedValue(baseRun({ status: 'PASS', cleanup_status: 'done' }));

  await act(async () => {
    render(<RunDetail runId="run-1" onBack={jest.fn()} />);
  });

  const toggle = await screen.findByLabelText('Auto cleanup');
  expect(toggle).toBeDisabled();
});

test('Clean Up Now is shown when a terminal run was skipped, and starts cleanup on click', async () => {
  mockGetRun.mockResolvedValue(baseRun({ status: 'FAIL', cleanup_status: 'skipped' }));
  mockCleanupRun.mockResolvedValue({ cleanup_status: 'cleaning' });

  await act(async () => {
    render(<RunDetail runId="run-1" onBack={jest.fn()} />);
  });

  const button = await screen.findByRole('button', { name: /clean up now/i });
  await act(async () => {
    button.click();
  });

  await waitFor(() => expect(mockCleanupRun).toHaveBeenCalledWith('run-1'));
});

test('Clean Up Now is replaced by a spinner while cleaning, and absent once done', async () => {
  mockGetRun.mockResolvedValue(baseRun({ status: 'FAIL', cleanup_status: 'cleaning' }));

  await act(async () => {
    render(<RunDetail runId="run-1" onBack={jest.fn()} />);
  });

  await screen.findByText(/cleaning up/i);
  expect(screen.queryByRole('button', { name: /clean up now/i })).not.toBeInTheDocument();
});

test('Clean Up Now is absent once cleanup is done', async () => {
  mockGetRun.mockResolvedValue(baseRun({ status: 'PASS', cleanup_status: 'done' }));

  await act(async () => {
    render(<RunDetail runId="run-1" onBack={jest.fn()} />);
  });

  await screen.findByLabelText('Auto cleanup');
  expect(screen.queryByRole('button', { name: /clean up now/i })).not.toBeInTheDocument();
  expect(screen.queryByText(/cleaning up/i)).not.toBeInTheDocument();
});
