import { fireEvent, render, screen } from '@testing-library/react';
import { AuthorizationPoliciesTab } from './AuthorizationPoliciesTab';
import * as client from '../../api/client';
import type { MaasAuthPolicy } from '../../api/client';

jest.mock('../../api/client');

// Monaco/CodeEditor isn't testable in jsdom (no ResizeObserver/workers) —
// stub it so these tests verify the tab opens it with the right content,
// not Monaco itself. Same pattern as every other maas/ tab's test file.
jest.mock('../RawYamlModal', () => ({
  RawYamlModal: ({ title, yamlText }: { title: string; yamlText: string | null }) => (
    <div data-testid="raw-yaml-modal">
      <span>{title}</span>
      <pre>{yamlText}</pre>
    </div>
  ),
}));

const mockGetAuthPolicies = client.getMaasAuthPolicies as jest.MockedFunction<
  typeof client.getMaasAuthPolicies
>;

const basePolicy: MaasAuthPolicy = {
  name: 'simulator-access',
  namespace: 'models-as-a-service',
  display_name: 'Simulator Access',
  description: 'Grants all authenticated users access to the simulator model',
  owner: { groups: ['system:authenticated'], users: [] },
  model_refs: [
    {
      name: 'facebook-opt-125m-simulated',
      namespace: 'llm',
      display_name: 'Facebook OPT 125M (Simulated)',
      model_exists: true,
      model_ready: true,
    },
  ],
  phase: 'Active',
  ready: true,
  raw_yaml: 'apiVersion: maas.opendatahub.io/v1alpha1\nkind: MaaSAuthPolicy\n',
};

test('shows an unavailable notice when the SA lacks RBAC', async () => {
  mockGetAuthPolicies.mockResolvedValue({ available: false, reason: 'forbidden', items: [] });

  render(<AuthorizationPoliciesTab />);

  expect(await screen.findByText(/MaaS visibility unavailable/i)).toBeInTheDocument();
});

test('renders policy rows with phase, target model, and granted-to', async () => {
  mockGetAuthPolicies.mockResolvedValue({ available: true, reason: null, items: [basePolicy] });

  render(<AuthorizationPoliciesTab />);

  expect(await screen.findByText('Simulator Access')).toBeInTheDocument();
  expect(screen.getByText('Grants all authenticated users access to the simulator model')).toBeInTheDocument();
  expect(screen.getByText('Active')).toBeInTheDocument();
  expect(screen.getByText('Facebook OPT 125M (Simulated)')).toBeInTheDocument();
  expect(screen.getByText('(llm/facebook-opt-125m-simulated)')).toBeInTheDocument();
  expect(screen.getByText('system:authenticated')).toBeInTheDocument();
});

test('flags a dangling model reference', async () => {
  const dangling: MaasAuthPolicy = {
    ...basePolicy,
    model_refs: [{ ...basePolicy.model_refs[0], model_exists: false, model_ready: null }],
  };
  mockGetAuthPolicies.mockResolvedValue({ available: true, reason: null, items: [dangling] });

  render(<AuthorizationPoliciesTab />);

  expect(await screen.findByText('⚠ model not found')).toBeInTheDocument();
});

test('flags a model ref that exists but is not ready', async () => {
  const notReady: MaasAuthPolicy = {
    ...basePolicy,
    model_refs: [{ ...basePolicy.model_refs[0], model_ready: false }],
  };
  mockGetAuthPolicies.mockResolvedValue({ available: true, reason: null, items: [notReady] });

  render(<AuthorizationPoliciesTab />);

  expect(await screen.findByText('⚠ model not ready')).toBeInTheDocument();
});

test('shows an empty-state message when there are no authorization policies', async () => {
  mockGetAuthPolicies.mockResolvedValue({ available: true, reason: null, items: [] });

  render(<AuthorizationPoliciesTab />);

  expect(await screen.findByText(/no authorization policies found/i)).toBeInTheDocument();
});

test('clicking View YAML opens the raw YAML modal for that policy', async () => {
  mockGetAuthPolicies.mockResolvedValue({ available: true, reason: null, items: [basePolicy] });

  render(<AuthorizationPoliciesTab />);

  fireEvent.click(await screen.findByRole('button', { name: /view yaml/i }));

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Auth Policy: Simulator Access');
  expect(modal).toHaveTextContent('kind: MaaSAuthPolicy');
});
