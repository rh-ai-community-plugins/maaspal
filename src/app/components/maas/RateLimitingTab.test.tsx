import { fireEvent, render, screen } from '@testing-library/react';
import { RateLimitingTab } from './RateLimitingTab';
import * as client from '../../api/client';
import type { MaasLimitador, MaasTokenRateLimitPolicy } from '../../api/client';

jest.mock('../../api/client');

jest.mock('../RawYamlModal', () => ({
  RawYamlModal: ({ title, yamlText }: { title: string; yamlText: string | null }) => (
    <div data-testid="raw-yaml-modal">
      <span>{title}</span>
      <pre>{yamlText}</pre>
    </div>
  ),
}));

const mockGetPolicies = client.getMaasRateLimitPolicies as jest.MockedFunction<
  typeof client.getMaasRateLimitPolicies
>;
const mockGetLimitador = client.getMaasLimitador as jest.MockedFunction<typeof client.getMaasLimitador>;

const policy: MaasTokenRateLimitPolicy = {
  name: 'maas-trlp-facebook-opt-125m-simulated',
  namespace: 'llm',
  target_kind: 'HTTPRoute',
  target_name: 'facebook-opt-125m-simulated-kserve-route',
  limit_names: ['models-as-a-service-simulator-free-facebook-opt-125m-simulated-tokens'],
  accepted: true,
  enforced: true,
  raw_yaml: 'kind: TokenRateLimitPolicy\n',
};

const limitador: MaasLimitador = {
  name: 'limitador',
  namespace: 'kuadrant-system',
  limit_count: 3,
  ready: true,
  service_host: 'limitador-limitador.kuadrant-system.svc.cluster.local',
  raw_yaml: 'kind: Limitador\n',
};

function mockBoth(
  policyResult: Awaited<ReturnType<typeof client.getMaasRateLimitPolicies>>,
  limitadorResult: Awaited<ReturnType<typeof client.getMaasLimitador>>,
) {
  mockGetPolicies.mockResolvedValue(policyResult);
  mockGetLimitador.mockResolvedValue(limitadorResult);
}

test('shows unavailable notices independently per section', async () => {
  mockBoth(
    { available: false, reason: 'forbidden', items: [] },
    { available: true, reason: null, items: [limitador] },
  );

  render(<RateLimitingTab />);

  expect(await screen.findByText(/MaaS visibility unavailable/i)).toBeInTheDocument();
  expect(await screen.findByText('limitador')).toBeInTheDocument();
});

test('renders token rate limit policy and limitador rows', async () => {
  mockBoth(
    { available: true, reason: null, items: [policy] },
    { available: true, reason: null, items: [limitador] },
  );

  render(<RateLimitingTab />);

  expect(await screen.findByText('maas-trlp-facebook-opt-125m-simulated')).toBeInTheDocument();
  expect(screen.getByText('HTTPRoute')).toBeInTheDocument();
  expect(screen.getByText('Accepted')).toBeInTheDocument();
  expect(screen.getByText('Enforced')).toBeInTheDocument();
  expect(screen.getByText('limitador')).toBeInTheDocument();
  expect(screen.getByText('3')).toBeInTheDocument();
  expect(screen.getByText('limitador-limitador.kuadrant-system.svc.cluster.local')).toBeInTheDocument();
});

test('shows empty-state messages when there are no objects', async () => {
  mockBoth(
    { available: true, reason: null, items: [] },
    { available: true, reason: null, items: [] },
  );

  render(<RateLimitingTab />);

  expect(await screen.findByText(/no tokenratelimitpolicy objects found/i)).toBeInTheDocument();
  expect(screen.getByText(/no limitador object found/i)).toBeInTheDocument();
});

test('clicking View YAML on a policy opens its YAML', async () => {
  mockBoth(
    { available: true, reason: null, items: [policy] },
    { available: true, reason: null, items: [] },
  );

  render(<RateLimitingTab />);

  fireEvent.click(await screen.findByRole('button', { name: /view yaml/i }));

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('TokenRateLimitPolicy: maas-trlp-facebook-opt-125m-simulated');
  expect(modal).toHaveTextContent('kind: TokenRateLimitPolicy');
});
