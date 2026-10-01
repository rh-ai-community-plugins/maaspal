import { render, screen } from '@testing-library/react';
import { RunHistory } from './RunHistory';
import * as client from '../api/client';
import { resetScenarioTitleCache } from '../scenarioTitles';

jest.mock('../api/client');

const mockListRuns = client.listRuns as jest.MockedFunction<typeof client.listRuns>;
const mockListScenarios = client.listScenarios as jest.MockedFunction<typeof client.listScenarios>;

beforeEach(() => {
  resetScenarioTitleCache();
  mockListScenarios.mockResolvedValue([
    {
      name: 'load_test',
      title: 'How does MaaS hold up under load?',
      description: '',
      config: {},
      category: 'Performance',
      previous_names: ['single_key_load', 'multi_key_load'],
    },
  ]);
});

test('renders run rows', async () => {
  mockListRuns.mockResolvedValue([
    {
      id: 'abc12345-0000-0000-0000-000000000000',
      scenario: 'single_key_load',
      status: 'PASS',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:01:00Z',
    },
  ]);

  render(<RunHistory />);

  // Shown by display title — and a pre-rename id still resolves through the
  // scenario's previous_names (single_key_load → load_test).
  expect(await screen.findByText('How does MaaS hold up under load?')).toBeInTheDocument();
  expect(screen.getByText('PASS')).toBeInTheDocument();
});

test('renders empty message when no runs', async () => {
  mockListRuns.mockResolvedValue([]);

  render(<RunHistory />);

  expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument();
});
