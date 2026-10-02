import { fireEvent, render, screen } from '@testing-library/react';
import { AccessSimulatorTab } from './AccessSimulatorTab';
import * as client from '../../api/client';
import type { MaasAuthPolicy, MaasModel, MaasSubscription } from '../../api/client';

jest.mock('../../api/client');

const mockGetModels = client.getMaasModels as jest.MockedFunction<typeof client.getMaasModels>;
const mockGetSubscriptions = client.getMaasSubscriptions as jest.MockedFunction<
  typeof client.getMaasSubscriptions
>;
const mockGetAuthPolicies = client.getMaasAuthPolicies as jest.MockedFunction<
  typeof client.getMaasAuthPolicies
>;

const modelX: MaasModel = {
  name: 'model-x',
  namespace: 'llm',
  display_name: 'Model X',
  description: '',
  kind: 'LLMInferenceService',
  hosting: 'internal',
  backing_name: 'model-x',
  phase: 'Ready',
  ready: true,
  endpoint: null,
  subscriptions: [],
  has_auth_policy: null,
  gateway_access_label: null,
  external_providers: [],
  serving: null,
  raw: {},
  raw_yaml: '',
  auth_policies: [],
  serving_raw_yaml: null,
};

const freeSub: MaasSubscription = {
  name: 'free-tier',
  namespace: 'models-as-a-service',
  display_name: 'Free Tier',
  description: '',
  priority: 10,
  owner: { groups: ['system:authenticated'], users: [] },
  model_refs: [
    {
      name: 'model-x',
      namespace: 'llm',
      token_rate_limits: [],
      display_name: 'Model X',
      model_exists: true,
      model_ready: true,
      has_auth_policy: null,
    },
  ],
  phase: 'Active',
  ready: true,
  priority_conflict: false,
  raw: {},
  raw_yaml: '',
};

const premiumSub: MaasSubscription = {
  ...freeSub,
  name: 'premium-tier',
  display_name: 'Premium Tier',
  priority: 20,
  owner: { groups: ['premium-users'], users: [] },
};

const authPolicy: MaasAuthPolicy = {
  name: 'model-x-access',
  namespace: 'models-as-a-service',
  display_name: 'Model X Access',
  description: '',
  phase: 'Active',
  owner: { groups: ['system:authenticated'], users: [] },
  model_refs: [
    { name: 'model-x', namespace: 'llm', display_name: 'Model X', model_exists: true, model_ready: true },
  ],
  ready: true,
  raw_yaml: '',
};

function mockAll(
  models: MaasModel[],
  subscriptions: MaasSubscription[],
  authPolicies: MaasAuthPolicy[],
) {
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: models });
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: subscriptions });
  mockGetAuthPolicies.mockResolvedValue({ available: true, reason: null, items: authPolicies });
}

async function getCandidateInput() {
  return screen.findByPlaceholderText(/system:authenticated/i);
}

// Types a value then commits it as a chip via Enter — mirrors how a user
// actually adds a candidate in CandidateTypeahead (typing alone doesn't
// commit; the input needs comma or Enter, same as a real chip-input).
function addCandidate(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

test('prompts for input before any groups are entered', async () => {
  mockAll([modelX], [freeSub], [authPolicy]);

  render(<AccessSimulatorTab />);

  expect(await screen.findByText(/enter one or more group\/user names/i)).toBeInTheDocument();
});

test('resolves the highest-priority matching subscription and marks the model reachable', async () => {
  mockAll([modelX], [freeSub, premiumSub], [authPolicy]);

  render(<AccessSimulatorTab />);

  addCandidate(await getCandidateInput(), 'system:authenticated');

  expect(await screen.findByText(/Free Tier \(p10\)/)).toBeInTheDocument();
  expect(screen.getByText('✓ reachable')).toBeInTheDocument();
  expect(screen.getByText('Model X Access')).toBeInTheDocument();
  // Committed as a chip, so the input's own value is cleared afterward (the
  // placeholder is gone once a chip exists, so query by its stable aria-label).
  expect(screen.getByRole('textbox', { name: /candidate groups or users/i })).toHaveValue('');
});

test('picks the higher-priority subscription when the group set matches multiple', async () => {
  const bothGroupsSub = { ...premiumSub, owner: { groups: ['system:authenticated'], users: [] } };
  mockAll([modelX], [freeSub, bothGroupsSub], [authPolicy]);

  render(<AccessSimulatorTab />);

  addCandidate(await getCandidateInput(), 'system:authenticated');

  expect(await screen.findByText(/Premium Tier \(p20\)/)).toBeInTheDocument();
  expect(screen.getByText(/beat 1 other matching subscription/)).toBeInTheDocument();
});

test('shows not reachable when there is quota but no matching auth policy', async () => {
  mockAll([modelX], [freeSub], []);

  render(<AccessSimulatorTab />);

  addCandidate(await getCandidateInput(), 'system:authenticated');

  expect(await screen.findByText('✗ not reachable')).toBeInTheDocument();
  expect(screen.getByText('none')).toBeInTheDocument();
});

test('shows no quota when the candidate group set matches no subscription', async () => {
  mockAll([modelX], [premiumSub], [authPolicy]);

  render(<AccessSimulatorTab />);

  addCandidate(await getCandidateInput(), 'some-other-group');

  expect(await screen.findByText(/no quota for this group set/i)).toBeInTheDocument();
  expect(screen.getByText('✗ not reachable')).toBeInTheDocument();
});

test('matches a subscription owned directly by a user, not just a group', async () => {
  const userOwnedSub: MaasSubscription = { ...freeSub, owner: { groups: [], users: ['alice'] } };
  const userOwnedPolicy: MaasAuthPolicy = { ...authPolicy, owner: { groups: [], users: ['alice'] } };
  mockAll([modelX], [userOwnedSub], [userOwnedPolicy]);

  render(<AccessSimulatorTab />);

  addCandidate(await getCandidateInput(), 'alice');

  expect(await screen.findByText(/Free Tier \(p10\)/)).toBeInTheDocument();
  expect(screen.getByText('✓ reachable')).toBeInTheDocument();
});

test('comma-terminated input commits a candidate without pressing Enter', async () => {
  mockAll([modelX], [freeSub], [authPolicy]);

  render(<AccessSimulatorTab />);

  const input = await getCandidateInput();
  fireEvent.change(input, { target: { value: 'system:authenticated,' } });

  expect(await screen.findByText(/Free Tier \(p10\)/)).toBeInTheDocument();
  expect(screen.getByText('system:authenticated')).toBeInTheDocument();
});

test('suggests known groups and users from subscriptions/auth policies as you type', async () => {
  const userOwnedSub: MaasSubscription = { ...premiumSub, owner: { groups: [], users: ['alice'] } };
  mockAll([modelX], [freeSub, userOwnedSub], [authPolicy]);

  render(<AccessSimulatorTab />);

  const input = await getCandidateInput();
  fireEvent.click(input);
  fireEvent.change(input, { target: { value: 'a' } });

  // "system:authenticated" (a group) and "alice" (a user) both contain "a".
  expect(await screen.findByRole('option', { name: 'system:authenticated' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'alice' })).toBeInTheDocument();
});

test('clicking a suggested option adds it as a candidate', async () => {
  mockAll([modelX], [freeSub], [authPolicy]);

  render(<AccessSimulatorTab />);

  const input = await getCandidateInput();
  fireEvent.click(input);
  fireEvent.change(input, { target: { value: 'system' } });

  fireEvent.click(await screen.findByRole('option', { name: 'system:authenticated' }));

  expect(await screen.findByText(/Free Tier \(p10\)/)).toBeInTheDocument();
});

test('shows an unavailable notice when models cannot be read', async () => {
  mockGetModels.mockResolvedValue({ available: false, reason: 'forbidden', items: [] });
  mockGetSubscriptions.mockResolvedValue({ available: true, reason: null, items: [] });
  mockGetAuthPolicies.mockResolvedValue({ available: true, reason: null, items: [] });

  render(<AccessSimulatorTab />);

  expect(await screen.findByText(/MaaS visibility unavailable/i)).toBeInTheDocument();
});
