import { fireEvent, render, screen } from '@testing-library/react';
import { NetworkingTab } from './NetworkingTab';
import * as client from '../../api/client';
import type { MaasGateway, MaasHttpRoute } from '../../api/client';

jest.mock('../../api/client');

jest.mock('../RawYamlModal', () => ({
  RawYamlModal: ({ title, yamlText }: { title: string; yamlText: string | null }) => (
    <div data-testid="raw-yaml-modal">
      <span>{title}</span>
      <pre>{yamlText}</pre>
    </div>
  ),
}));

const mockGetGateways = client.getMaasGateways as jest.MockedFunction<typeof client.getMaasGateways>;
const mockGetHttpRoutes = client.getMaasHttpRoutes as jest.MockedFunction<typeof client.getMaasHttpRoutes>;

const gateway: MaasGateway = {
  name: 'maas-default-gateway',
  namespace: 'openshift-ingress',
  gateway_class: 'openshift-default',
  address: 'a5022c964ce964d4f85bc428e4f4a58f-514146043.us-east-2.elb.amazonaws.com',
  programmed: true,
  raw_yaml: 'kind: Gateway\n',
};

const route: MaasHttpRoute = {
  name: 'facebook-opt-125m-simulated-kserve-route',
  namespace: 'llm',
  parent_gateway: 'maas-default-gateway',
  parent_gateway_namespace: 'openshift-ingress',
  owning_model: 'facebook-opt-125m-simulated',
  raw_yaml: 'kind: HTTPRoute\n',
};

function mockBoth(
  gatewayResult: Awaited<ReturnType<typeof client.getMaasGateways>>,
  routeResult: Awaited<ReturnType<typeof client.getMaasHttpRoutes>>,
) {
  mockGetGateways.mockResolvedValue(gatewayResult);
  mockGetHttpRoutes.mockResolvedValue(routeResult);
}

test('shows unavailable notices independently per section', async () => {
  mockBoth(
    { available: false, reason: 'forbidden', items: [] },
    { available: true, reason: null, items: [route] },
  );

  render(<NetworkingTab />);

  expect(await screen.findByText(/MaaS visibility unavailable/i)).toBeInTheDocument();
  expect(await screen.findByText('facebook-opt-125m-simulated-kserve-route')).toBeInTheDocument();
});

test('renders gateway and http route rows, tracing a route to its model', async () => {
  mockBoth(
    { available: true, reason: null, items: [gateway] },
    { available: true, reason: null, items: [route] },
  );

  render(<NetworkingTab />);

  // "maas-default-gateway" appears both as the Gateway row's name and as the
  // HTTPRoute row's parent-gateway reference — assert on the count instead
  // of a single-match query.
  expect(await screen.findAllByText('maas-default-gateway')).toHaveLength(2);
  expect(screen.getByText('openshift-default')).toBeInTheDocument();
  expect(screen.getByText('Programmed')).toBeInTheDocument();
  expect(screen.getByText('facebook-opt-125m-simulated-kserve-route')).toBeInTheDocument();
  expect(screen.getByText('facebook-opt-125m-simulated')).toBeInTheDocument();
});

test('shows empty-state messages when there are no objects', async () => {
  mockBoth(
    { available: true, reason: null, items: [] },
    { available: true, reason: null, items: [] },
  );

  render(<NetworkingTab />);

  expect(await screen.findByText(/no gateway objects found/i)).toBeInTheDocument();
  expect(screen.getByText(/no httproute objects found/i)).toBeInTheDocument();
});

test('clicking View YAML on a route opens its YAML', async () => {
  mockBoth(
    { available: true, reason: null, items: [] },
    { available: true, reason: null, items: [route] },
  );

  render(<NetworkingTab />);

  fireEvent.click(await screen.findByRole('button', { name: /view yaml/i }));

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('HTTPRoute: facebook-opt-125m-simulated-kserve-route');
  expect(modal).toHaveTextContent('kind: HTTPRoute');
});
