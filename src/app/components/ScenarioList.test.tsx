import { render, screen, within } from '@testing-library/react';
import { ScenarioList } from './ScenarioList';
import * as client from '../api/client';
import type { Scenario } from '../api/client';

jest.mock('../api/client');

const mockListScenarios = client.listScenarios as jest.MockedFunction<typeof client.listScenarios>;

const smoke: Scenario = {
  name: 'smoke_test',
  title: 'Is MaaS working end to end?',
  summary: 'The 30-second check.',
  description: 'long description',
  config: {},
  category: 'Quick check',
  kind: 'verify',
  mutates: ['api_keys'],
  est_duration: '~30 s',
};

test('shows plain-language titles and summaries, grouped by category', async () => {
  mockListScenarios.mockResolvedValue([
    smoke,
    { name: 'rate_limit_window_recovery', title: 'Does access come back?', description: 'd', config: {}, category: 'Rate limits' },
  ]);

  render(<ScenarioList onRun={() => undefined} />);

  expect(await screen.findByText('Is MaaS working end to end?')).toBeInTheDocument();
  expect(screen.getByText('The 30-second check.')).toBeInTheDocument();
  expect(screen.getByText('Does access come back?')).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: /^run /i })).toHaveLength(2);

  expect(screen.getByText('Quick check')).toBeInTheDocument();
  expect(screen.getByText('Rate limits')).toBeInTheDocument();
});

test('falls back to the title-cased name when a scenario has no title', async () => {
  mockListScenarios.mockResolvedValue([
    { name: 'my_scenario', description: 'A user-authored scenario', config: {}, category: 'Custom' },
  ]);

  render(<ScenarioList onRun={() => undefined} />);

  expect(await screen.findByText('My Scenario')).toBeInTheDocument();
  expect(screen.getByText('A user-authored scenario')).toBeInTheDocument();
});

test('badges say whether a scenario creates temporary resources or needs extra RBAC', async () => {
  mockListScenarios.mockResolvedValue([
    smoke,
    {
      name: 'denied_without_auth_policy',
      title: 'No auth policy → access denied?',
      description: 'd',
      config: {},
      category: 'Access control',
      kind: 'explore',
      mutates: ['models', 'subscriptions'],
      needs_rbac: ['user-provisioning'],
    },
  ]);

  render(<ScenarioList onRun={() => undefined} />);

  expect(await screen.findByText('Uses your setup')).toBeInTheDocument();
  expect(screen.getByText('Creates temporary resources')).toBeInTheDocument();
  expect(screen.getByText('Needs extra RBAC')).toBeInTheDocument();
  expect(screen.getByText('~30 s')).toBeInTheDocument();
});

test('within a category, verify scenarios are listed before explore ones', async () => {
  mockListScenarios.mockResolvedValue([
    { name: 'b_explore', title: 'Explore one', description: 'd', config: {}, category: 'Rate limits', kind: 'explore' },
    { name: 'a_verify', title: 'Verify one', description: 'd', config: {}, category: 'Rate limits', kind: 'verify' },
  ]);

  render(<ScenarioList onRun={() => undefined} />);

  await screen.findByText('Verify one');
  const titles = screen.getAllByText(/one$/).map((el) => el.textContent);
  expect(titles).toEqual(['Verify one', 'Explore one']);
});

test('always shows an empty Custom section when no scenario uses it', async () => {
  mockListScenarios.mockResolvedValue([smoke]);

  render(<ScenarioList onRun={() => undefined} />);

  await screen.findByText('Is MaaS working end to end?');
  expect(screen.getByText('Custom')).toBeInTheDocument();
  expect(screen.getByText(/no custom scenarios yet/i)).toBeInTheDocument();
});

test('a scenario with an unrecognized category falls into Custom', async () => {
  mockListScenarios.mockResolvedValue([
    { name: 'my_scenario', description: 'A user-authored scenario', config: {}, category: 'Something Else' },
  ]);

  render(<ScenarioList onRun={() => undefined} />);

  const card = (await screen.findByText('My Scenario')).closest('.maaspal-scenario-card') as HTMLElement;
  expect(within(card).getByRole('button', { name: /run my scenario/i })).toBeInTheDocument();
  expect(screen.queryByText(/no custom scenarios yet/i)).not.toBeInTheDocument();
});

test('renders empty state when no scenarios are returned', async () => {
  mockListScenarios.mockResolvedValue([]);

  render(<ScenarioList onRun={() => undefined} />);

  expect(await screen.findByText(/no scenarios available/i)).toBeInTheDocument();
});
