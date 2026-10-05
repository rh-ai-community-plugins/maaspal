import { fireEvent, render, screen } from '@testing-library/react';
import { AccessControlTab } from './AccessControlTab';
import * as client from '../../api/client';
import type { MaasAccessRow } from '../../api/client';

jest.mock('../../api/client');

jest.mock('../RawYamlModal', () => ({
  RawYamlModal: ({ title, yamlText }: { title: string; yamlText: string | null }) => (
    <div data-testid="raw-yaml-modal">
      <span>{title}</span>
      <pre>{yamlText}</pre>
    </div>
  ),
}));

const mockGetAccess = client.getMaasAccess as jest.MockedFunction<typeof client.getMaasAccess>;

const matchedRow: MaasAccessRow = {
  name: 'team-a',
  users: ['alice', 'bob'],
  raw_yaml: 'kind: Group\nmetadata:\n  name: team-a\n',
  subscriptions: [
    { name: 'sub-a', display_name: 'Team A Sub', priority: 10, raw_yaml: 'kind: MaaSSubscription\nmetadata:\n  name: sub-a\n' },
  ],
  auth_policies: [
    { name: 'ap-a', display_name: 'Team A Access', ready: true, raw_yaml: 'kind: MaaSAuthPolicy\nmetadata:\n  name: ap-a\n' },
  ],
  quota_without_access: [],
  access_without_quota: [],
};

test('shows an unavailable notice when the SA lacks RBAC', async () => {
  mockGetAccess.mockResolvedValue({ available: false, reason: 'forbidden', items: [] });

  render(<AccessControlTab />);

  expect(await screen.findByText(/MaaS visibility unavailable/i)).toBeInTheDocument();
});

test('renders a matched group with no mismatches', async () => {
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [matchedRow] });

  render(<AccessControlTab />);

  expect(await screen.findByText('team-a')).toBeInTheDocument();
  expect(screen.getByText('alice')).toBeInTheDocument();
  expect(screen.getByText(/Team A Sub/)).toBeInTheDocument();
  expect(screen.getByText('Team A Access')).toBeInTheDocument();
});

test('flags quota granted without a matching auth policy', async () => {
  const row: MaasAccessRow = { ...matchedRow, auth_policies: [], quota_without_access: ['llm/model-x'] };
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [row] });

  render(<AccessControlTab />);

  expect(await screen.findByText(/quota, no gateway access: llm\/model-x/i)).toBeInTheDocument();
});

test('flags gateway access without a matching subscription', async () => {
  const row: MaasAccessRow = { ...matchedRow, subscriptions: [], access_without_quota: ['llm/model-y'] };
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [row] });

  render(<AccessControlTab />);

  expect(await screen.findByText(/gateway access, no quota: llm\/model-y/i)).toBeInTheDocument();
});

test('shows unresolved members when Groups is unavailable', async () => {
  const row: MaasAccessRow = { ...matchedRow, users: null };
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [row] });

  render(<AccessControlTab />);

  expect(await screen.findByText(/unknown \(groups unavailable\)/i)).toBeInTheDocument();
});

test('shows an empty-state message when there are no rows', async () => {
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [] });

  render(<AccessControlTab />);

  expect(await screen.findByText(/no groups reference/i)).toBeInTheDocument();
});

test('does not offer a group YAML view when Groups is unavailable', async () => {
  const row: MaasAccessRow = { ...matchedRow, users: null, raw_yaml: null };
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [row] });

  render(<AccessControlTab />);

  await screen.findByText('team-a');
  expect(screen.queryByRole('button', { name: /view yaml/i })).not.toBeInTheDocument();
});

test('clicking the group View YAML opens the group CR', async () => {
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [matchedRow] });

  render(<AccessControlTab />);

  fireEvent.click(await screen.findByRole('button', { name: /view yaml/i }));

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Group: team-a');
  expect(modal).toHaveTextContent('kind: Group');
});

test('clicking a subscription chip opens that subscription\'s YAML', async () => {
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [matchedRow] });

  render(<AccessControlTab />);

  fireEvent.click(await screen.findByText(/Team A Sub/));

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Subscription: Team A Sub');
  expect(modal).toHaveTextContent('kind: MaaSSubscription');
});

test('clicking an auth policy chip opens that policy\'s YAML', async () => {
  mockGetAccess.mockResolvedValue({ available: true, reason: null, items: [matchedRow] });

  render(<AccessControlTab />);

  fireEvent.click(await screen.findByText('Team A Access'));

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Auth Policy: Team A Access');
  expect(modal).toHaveTextContent('kind: MaaSAuthPolicy');
});
