import { fireEvent, render, screen } from '@testing-library/react';
import { PlatformConfigTab } from './PlatformConfigTab';
import * as client from '../../api/client';
import type { MaasPlatform } from '../../api/client';

jest.mock('../../api/client');

jest.mock('../RawYamlModal', () => ({
  RawYamlModal: ({ title, yamlText }: { title: string; yamlText: string | null }) => (
    <div data-testid="raw-yaml-modal">
      <span>{title}</span>
      <pre>{yamlText}</pre>
    </div>
  ),
}));

const mockGetPlatform = client.getMaasPlatform as jest.MockedFunction<typeof client.getMaasPlatform>;

const fullPlatform: MaasPlatform = {
  tenants: {
    available: true,
    reason: null,
    items: [
      {
        name: 'default-tenant',
        namespace: 'models-as-a-service',
        gateway_ref: { name: 'maas-default-gateway', namespace: 'openshift-ingress' },
        max_api_key_expiration_days: 90,
        telemetry_enabled: true,
        phase: 'Active',
        raw_yaml: 'kind: Tenant\n',
      },
    ],
  },
  data_science_cluster: {
    available: true,
    reason: null,
    item: {
      name: 'default-dsc',
      maas_management_state: 'Managed',
      maas_field_path: 'aigateway.modelsAsAService',
      raw_yaml: 'kind: DataScienceCluster\n',
    },
  },
  odh_dashboard_config: {
    available: true,
    reason: null,
    item: {
      name: 'odh-dashboard-config',
      model_as_service: true,
      external_models: true,
      gen_ai_studio: false,
      observability_dashboard: true,
      raw_yaml: 'kind: OdhDashboardConfig\n',
    },
  },
};

test('renders tenant, DataScienceCluster, and OdhDashboardConfig sections', async () => {
  mockGetPlatform.mockResolvedValue(fullPlatform);

  render(<PlatformConfigTab />);

  expect(await screen.findByText('default-tenant')).toBeInTheDocument();
  expect(screen.getByText('90')).toBeInTheDocument();
  expect(screen.getByText('MaaS: Managed')).toBeInTheDocument();
  expect(screen.getByText('aigateway.modelsAsAService')).toBeInTheDocument();
  expect(screen.getByText(/model as a service/i)).toBeInTheDocument();
});

test('shows unavailable notices independently per section', async () => {
  const partial: MaasPlatform = {
    ...fullPlatform,
    tenants: { available: false, reason: 'forbidden', items: [] },
    odh_dashboard_config: { available: false, reason: 'not_installed', item: null },
  };
  mockGetPlatform.mockResolvedValue(partial);

  render(<PlatformConfigTab />);

  const notices = await screen.findAllByText(/MaaS visibility unavailable/i);
  expect(notices).toHaveLength(2);
  expect(await screen.findByText('MaaS: Managed')).toBeInTheDocument();
});

test('flags MaaS not managed on the DataScienceCluster', async () => {
  const notManaged: MaasPlatform = {
    ...fullPlatform,
    data_science_cluster: {
      available: true,
      reason: null,
      item: { name: 'default-dsc', maas_management_state: null, maas_field_path: null, raw_yaml: 'kind: DataScienceCluster\n' },
    },
  };
  mockGetPlatform.mockResolvedValue(notManaged);

  render(<PlatformConfigTab />);

  expect(await screen.findByText('MaaS: not configured')).toBeInTheDocument();
});

test('clicking View YAML on the tenant opens its YAML', async () => {
  mockGetPlatform.mockResolvedValue(fullPlatform);

  render(<PlatformConfigTab />);

  // Three sections each have their own "View YAML" — the tenant's is first.
  const buttons = await screen.findAllByRole('button', { name: /view yaml/i });
  fireEvent.click(buttons[0]);

  const modal = await screen.findByTestId('raw-yaml-modal');
  expect(modal).toHaveTextContent('Tenant: default-tenant');
  expect(modal).toHaveTextContent('kind: Tenant');
});
