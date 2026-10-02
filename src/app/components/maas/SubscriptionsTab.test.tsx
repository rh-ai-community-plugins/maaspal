import { fireEvent, render, screen } from '@testing-library/react';
import { SubscriptionsTab } from './SubscriptionsTab';
import * as client from '../../api/client';
import type { MaasSubscription } from '../../api/client';

jest.mock('../../api/client');

// Monaco/CodeEditor isn't testable in jsdom (no ResizeObserver/workers) and
// isn't exercised anywhere else in this test suite either — stub it so these
// tests verify SubscriptionsTab opens it with the right content, not Monaco itself.
jest.mock('../RawYamlModal', () => ({
  RawYamlModal: ({ title, yamlText }: { title: string; yamlText: string | null }) => (
    <div data-testid="raw-yaml-modal">
      <span>{title}</span>
      <pre>{yamlText}</pre>
    </div>
  ),
}));

const mockGetSubscriptions = client.getMaasSubscriptions as jest.MockedFunction<
  typeof client.getMaasSubscriptions
>;

const baseSubscription: MaasSubscription = {
  name: 'simulator-free',
  namespace: 'models-as-a-service',
  display_name: 'Simulator Free Tier',
  description: 'Free tier: 100 tokens/min for all authenticated users',
  priority: 10,
  owner: { groups: ['system:authenticated'], users: [] },
  model_refs: [
    {
      name: 'facebook-opt-125m-simulated',
      namespace: 'llm',
      token_rate_limits: [{ limit: 100, window: '1m' }],
      display_name: 'Facebook OPT 125M (Simulated)',
      model_exists: true,
      model_ready: true,
      has_auth_policy: true,
    },
  ],
  phase: 'Active',
  ready: true,
  priority_conflict: false,
  raw: {},
  raw_yaml: 'apiVersion: maas.opendatahub.io/v1alpha1\nkind: MaaSSubscription\n',
};

test('shows an unavailable notice when the SA lacks RBAC', async () => {
  mockGetSubscriptions.mockResolvedValue({ available: false, reason: 'forbidden', items: [] });

  render(<SubscriptionsTab />);

  expect(await screen.findByText(/MaaS visibility unavailable/i)).toBeInTheDocument();
  expect(screen.getByText(/rbac\.maasReadonly/i)).toBeInTheDocument();
});

test('renders subscription rows with priority, phase, and rate limits', async () => {
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [baseSubscription] });

  render(<SubscriptionsTab />);

  expect(await screen.findByText('Simulator Free Tier')).toBeInTheDocument();
  expect(screen.getByText('10')).toBeInTheDocument();
  expect(screen.getByText('Active')).toBeInTheDocument();
  expect(screen.getByText('Facebook OPT 125M (Simulated)')).toBeInTheDocument();
  expect(screen.getByText('(llm/facebook-opt-125m-simulated)')).toBeInTheDocument();
  expect(screen.getByText('100 / 1m')).toBeInTheDocument();
  expect(screen.getByText('✓ has auth policy')).toBeInTheDocument();
  expect(screen.getByText('system:authenticated')).toBeInTheDocument();
});

test('renders multiple model refs, each with their own (possibly multi-tier) rate limits', async () => {
  // Rate limits belong to each model ref, not the subscription as a whole —
  // different models under one subscription can carry different limits, and
  // one model ref can carry more than one tier (e.g. burst + sustained).
  const multiTier: MaasSubscription = {
    ...baseSubscription,
    model_refs: [
      {
        ...baseSubscription.model_refs[0],
        token_rate_limits: [
          { limit: 100, window: '1m' },
          { limit: 2000, window: '1h' },
        ],
      },
      {
        name: 'granite-8b',
        namespace: 'llm',
        token_rate_limits: [{ limit: 20, window: '1m' }],
        display_name: 'Granite 8B',
        model_exists: true,
        model_ready: true,
        has_auth_policy: false,
      },
    ],
  };
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [multiTier] });

  render(<SubscriptionsTab />);

  expect(await screen.findByText('Facebook OPT 125M (Simulated)')).toBeInTheDocument();
  expect(screen.getByText('100 / 1m')).toBeInTheDocument();
  expect(screen.getByText('2000 / 1h')).toBeInTheDocument();
  expect(screen.getByText('Granite 8B')).toBeInTheDocument();
  expect(screen.getByText('20 / 1m')).toBeInTheDocument();
  expect(screen.getByText('⚠ no auth policy')).toBeInTheDocument();
});

test('flags a missing auth policy on a covered model', async () => {
  const noAuthPolicy: MaasSubscription = {
    ...baseSubscription,
    model_refs: [{ ...baseSubscription.model_refs[0], has_auth_policy: false }],
  };
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [noAuthPolicy] });

  render(<SubscriptionsTab />);

  expect(await screen.findByText('⚠ no auth policy')).toBeInTheDocument();
});

test('flags a dangling model reference', async () => {
  const danglingRef: MaasSubscription = {
    ...baseSubscription,
    model_refs: [{ ...baseSubscription.model_refs[0], model_exists: false, model_ready: null }],
  };
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [danglingRef] });

  render(<SubscriptionsTab />);

  expect(await screen.findByText('⚠ model not found')).toBeInTheDocument();
});

test('flags a priority conflict', async () => {
  const conflicting = { ...baseSubscription, priority_conflict: true };
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [conflicting] });

  render(<SubscriptionsTab />);

  expect(await screen.findByText(/priority conflict/i)).toBeInTheDocument();
});

test('shows an empty-state message when there are no subscriptions', async () => {
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [] });

  render(<SubscriptionsTab />);

  expect(await screen.findByText(/no subscriptions found/i)).toBeInTheDocument();
});

test('clicking View YAML opens the raw YAML modal for that subscription', async () => {
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [baseSubscription] });

  render(<SubscriptionsTab />);

  fireEvent.click(await screen.findByRole('button', { name: /view yaml/i }));

  // The modal is lazy-loaded (Suspense) so it doesn't ship Monaco in the main
  // bundle — see SubscriptionsTab.tsx — so it only appears after a tick.
  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Subscription: Simulator Free Tier');
  expect(modal).toHaveTextContent('kind: MaaSSubscription');
});
