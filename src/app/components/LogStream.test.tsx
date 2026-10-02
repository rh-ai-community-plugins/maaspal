import { render, screen, act } from '@testing-library/react';
import { LogStream } from './LogStream';

beforeEach(() => {
  jest.useFakeTimers();
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ lines: [], done: false, next_offset: 0 }),
  } as unknown as Response);
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test('renders waiting message initially', async () => {
  await act(async () => {
    render(<LogStream runId="run-1" />);
  });
  expect(screen.getByText(/waiting for logs/i)).toBeInTheDocument();
});

test('displays log lines returned by poll', async () => {
  (global.fetch as jest.Mock)
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        lines: ['[harness] starting', '[task] ok'],
        done: false,
        next_offset: 2,
      }),
    } as unknown as Response)
    .mockResolvedValue({
      ok: true,
      json: async () => ({ lines: [], done: false, next_offset: 2 }),
    } as unknown as Response);

  await act(async () => {
    render(<LogStream runId="run-1" />);
    await Promise.resolve();
  });

  expect(screen.getByText(/\[harness\] starting/)).toBeInTheDocument();
  expect(screen.getByText(/\[task\] ok/)).toBeInTheDocument();
});
