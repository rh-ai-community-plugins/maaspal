import { fireEvent, render, screen } from '@testing-library/react';
import { ModelsTab } from './ModelsTab';
import * as client from '../../api/client';
import type { MaasModel } from '../../api/client';

jest.mock('../../api/client');

jest.mock('../RawYamlModal', () => ({
  RawYamlModal: ({ title, yamlText }: { title: string; yamlText: string | null }) => (
    <div data-testid="raw-yaml-modal">
      <span>{title}</span>
      <pre>{yamlText}</pre>
    </div>
  ),
}));

const mockGetModels = client.getMaasModels as jest.MockedFunction<typeof client.getMaasModels>;

const baseModel: MaasModel = {
  name: 'facebook-opt-125m-simulated',
  namespace: 'llm',
  display_name: 'Facebook OPT 125M (Simulated)',
  description: 'CPU-only simulator for testing MaaS without a real LLM',
  kind: 'LLMInferenceService',
  hosting: 'internal',
  backing_name: 'facebook-opt-125m-simulated',
  phase: 'Ready',
  ready: true,
  endpoint: 'https://maas.example.com/llm/facebook-opt-125m-simulated',
  subscriptions: [
    { name: 'simulator-free', display_name: 'Simulator Free Tier', description: null },
  ],
  has_auth_policy: true,
  auth_policies: [
    {
      name: 'simulator-access',
      namespace: 'models-as-a-service',
      display_name: 'Simulator Access',
      ready: true,
      raw_yaml: 'kind: MaaSAuthPolicy\nmetadata:\n  name: simulator-access\n',
    },
  ],
  gateway_access_label: true,
  external_providers: [],
  serving: { replicas: 1, resources: null, conditions: [] },
  raw: {},
  raw_yaml: 'maasModelRef:\n  kind: MaaSModelRef\n',
  serving_raw_yaml: 'kind: LLMInferenceService\nmetadata:\n  name: facebook-opt-125m-simulated\n',
};

test('shows an unavailable notice when the SA lacks RBAC', async () => {
  mockGetModels.mockResolvedValue({ available: false, reason: 'forbidden', items: [] });

  render(<ModelsTab />);

  expect(await screen.findByText(/MaaS visibility unavailable/i)).toBeInTheDocument();
});

test('renders model rows with hosting, status, endpoint, and subscriptions', async () => {
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [baseModel] });

  render(<ModelsTab />);

  expect(await screen.findByText('Facebook OPT 125M (Simulated)')).toBeInTheDocument();
  expect(screen.getByText('Internal')).toBeInTheDocument();
  expect(screen.getByText('llm')).toBeInTheDocument();
  expect(screen.getByText('LLMInferenceService')).toBeInTheDocument();
  expect(screen.getByText('Ready')).toBeInTheDocument();
  expect(screen.getByText('https://maas.example.com/llm/facebook-opt-125m-simulated')).toBeInTheDocument();
  expect(screen.getByText('Simulator Free Tier')).toBeInTheDocument();
  expect(screen.getByText('1 replica')).toBeInTheDocument();
  expect(screen.getByText('Namespace')).toBeInTheDocument();
  expect(screen.getByText('✓ gateway-access')).toBeInTheDocument();
  expect(screen.getByText('✓ Simulator Access')).toBeInTheDocument();
});

test('clicking the auth policy\'s View YAML link opens its raw YAML', async () => {
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [baseModel] });

  render(<ModelsTab />);

  // Two "View YAML" links exist on this row (the model's own, and the auth
  // policy's) — the auth policy one is the first, since it renders earlier
  // in the row's column order.
  const viewYamlLinks = await screen.findAllByRole('button', { name: /view yaml/i });
  expect(viewYamlLinks).toHaveLength(2);
  fireEvent.click(viewYamlLinks[0]);

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Auth Policy: Simulator Access');
  expect(modal).toHaveTextContent('kind: MaaSAuthPolicy');
});

test('flags a namespace missing the gateway-access label', async () => {
  const noLabel: MaasModel = { ...baseModel, gateway_access_label: false };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [noLabel] });

  render(<ModelsTab />);

  expect(await screen.findByText('⚠ missing gateway-access label')).toBeInTheDocument();
});

test('flags a model with no matching auth policy', async () => {
  const noAuthPolicy: MaasModel = { ...baseModel, has_auth_policy: false, auth_policies: [] };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [noAuthPolicy] });

  render(<ModelsTab />);

  expect(await screen.findByText('⚠ no auth policy')).toBeInTheDocument();
});

test('shows unknown (not a false negative) when auth-policy/label state cannot be read', async () => {
  const unknown: MaasModel = { ...baseModel, has_auth_policy: null, gateway_access_label: null };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [unknown] });

  render(<ModelsTab />);

  expect(await screen.findByText('unknown')).toBeInTheDocument();
  expect(await screen.findByText('gateway-access: unknown')).toBeInTheDocument();
  expect(screen.queryByText('⚠ missing gateway-access label')).not.toBeInTheDocument();
  expect(screen.queryByText('✓ gateway-access')).not.toBeInTheDocument();
});

test('shows an External badge for externally-hosted models', async () => {
  const external: MaasModel = {
    ...baseModel,
    name: 'gpt-4o-proxy',
    display_name: 'GPT-4o (external)',
    kind: 'ExternalModel',
    hosting: 'external',
    serving: null,
    serving_raw_yaml: null,
  };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [external] });

  render(<ModelsTab />);

  expect(await screen.findByText('External')).toBeInTheDocument();
  expect(screen.getByText('ExternalModel')).toBeInTheDocument();
});

test('flags a correctly-labeled external provider credential secret', async () => {
  const external: MaasModel = {
    ...baseModel,
    name: 'gpt-4o-proxy',
    display_name: 'GPT-4o (external)',
    kind: 'ExternalModel',
    hosting: 'external',
    serving: null,
    serving_raw_yaml: null,
    external_providers: [
      {
        provider_name: 'openai',
        target_model: 'gpt-4o-mini',
        api_format: 'openai-chat',
        path: '/v1/chat/completions',
        endpoint: 'api.openai.com',
        credential_secret_name: 'openai-api-key',
        credential_secret_label_ok: true,
        raw_yaml: 'kind: ExternalProvider\nmetadata:\n  name: openai\n',
      },
    ],
  };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [external] });

  render(<ModelsTab />);

  expect(await screen.findByText('openai')).toBeInTheDocument();
  expect(screen.getByText(/gpt-4o-mini/)).toBeInTheDocument();
  expect(screen.getByText('✓ credential secret labeled')).toBeInTheDocument();
});

test('clicking a provider\'s View YAML link opens its own raw YAML', async () => {
  const external: MaasModel = {
    ...baseModel,
    name: 'gpt-4o-proxy',
    display_name: 'GPT-4o (external)',
    kind: 'ExternalModel',
    hosting: 'external',
    serving: null,
    serving_raw_yaml: null,
    external_providers: [
      {
        provider_name: 'openai',
        target_model: 'gpt-4o-mini',
        api_format: 'openai-chat',
        path: '/v1/chat/completions',
        endpoint: 'api.openai.com',
        credential_secret_name: 'openai-api-key',
        credential_secret_label_ok: true,
        raw_yaml: 'kind: ExternalProvider\nmetadata:\n  name: openai\n',
      },
    ],
  };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [external] });

  render(<ModelsTab />);

  // Three "View YAML" links exist on this row: the provider's, the auth
  // policy's, and the model's own — the provider's is first, since the
  // External provider column renders before Auth policy and Actions.
  const viewYamlLinks = await screen.findAllByRole('button', { name: /^view yaml$/i });
  expect(viewYamlLinks).toHaveLength(3);
  fireEvent.click(viewYamlLinks[0]);

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Provider: openai');
  expect(modal).toHaveTextContent('kind: ExternalProvider');
});

test('clicking View Deployment YAML opens the LLMInferenceService raw YAML', async () => {
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [baseModel] });

  render(<ModelsTab />);

  fireEvent.click(await screen.findByRole('button', { name: /view deployment yaml/i }));

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Deployment: Facebook OPT 125M (Simulated)');
  expect(modal).toHaveTextContent('kind: LLMInferenceService');
});

test('flags an external provider whose credential secret is missing the required label', async () => {
  const external: MaasModel = {
    ...baseModel,
    name: 'gpt-4o-proxy',
    hosting: 'external',
    serving: null,
    serving_raw_yaml: null,
    external_providers: [
      {
        provider_name: 'openai',
        target_model: 'gpt-4o-mini',
        api_format: null,
        path: null,
        endpoint: null,
        credential_secret_name: 'openai-api-key',
        credential_secret_label_ok: false,
        raw_yaml: null,
      },
    ],
  };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [external] });

  render(<ModelsTab />);

  expect(await screen.findByText('⚠ credential secret missing label')).toBeInTheDocument();
});

test('shows unknown when the credential secret cannot be read', async () => {
  const external: MaasModel = {
    ...baseModel,
    name: 'gpt-4o-proxy',
    hosting: 'external',
    serving: null,
    serving_raw_yaml: null,
    external_providers: [
      {
        provider_name: 'openai',
        target_model: 'gpt-4o-mini',
        api_format: null,
        path: null,
        endpoint: null,
        credential_secret_name: 'openai-api-key',
        credential_secret_label_ok: null,
        raw_yaml: null,
      },
    ],
  };
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [external] });

  render(<ModelsTab />);

  expect(await screen.findByText('credential secret: unknown')).toBeInTheDocument();
});

test('shows an empty-state message when there are no models', async () => {
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [] });

  render(<ModelsTab />);

  expect(await screen.findByText(/no models found/i)).toBeInTheDocument();
});

test('clicking View YAML opens the raw YAML modal for that model', async () => {
  mockGetModels.mockResolvedValue({ available: true, reason: null, items: [baseModel] });

  render(<ModelsTab />);

  // Two "View YAML" links exist on this row (the model's own, in the Actions
  // column, and the auth policy's) — the model's is the last one.
  const viewYamlLinks = await screen.findAllByRole('button', { name: /view yaml/i });
  fireEvent.click(viewYamlLinks[viewYamlLinks.length - 1]);

  // The modal is lazy-loaded (Suspense) so it doesn't ship Monaco in the main
  // bundle — see ModelsTab.tsx — so it only appears after a tick.
  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Model: Facebook OPT 125M (Simulated)');
  expect(modal).toHaveTextContent('kind: MaaSModelRef');
});
