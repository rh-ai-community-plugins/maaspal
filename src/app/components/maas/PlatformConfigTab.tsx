import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Button, Card, CardBody, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import { getMaasPlatform, type MaasPlatform } from '../../api/client';
import { MaasUnavailableNotice } from './MaasUnavailableNotice';

const RawYamlModal = lazy(() =>
  import('../RawYamlModal').then((m) => ({ default: m.RawYamlModal })),
);

const POLL_INTERVAL_MS = 25000;

interface YamlTarget {
  title: string;
  downloadFileName: string;
  yamlText: string;
}

function FlagLabel({ label, value }: { label: string; value: boolean | null }) {
  return (
    <Label isCompact color={value ? 'green' : value === false ? 'grey' : 'grey'} style={{ marginRight: '0.4rem', marginBottom: '0.3rem' }}>
      {value ? '✓' : '✗'} {label}
    </Label>
  );
}

export function PlatformConfigTab() {
  const [platform, setPlatform] = useState<MaasPlatform | null>(null);
  const [yamlTarget, setYamlTarget] = useState<YamlTarget | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    setPlatform(await getMaasPlatform());
  }

  useEffect(() => {
    void fetchData();
    intervalRef.current = setInterval(() => { void fetchData(); }, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current);
    };
  }, []);

  if (platform === null) return <Spinner size="md" aria-label="Loading platform config" />;

  const { tenants, data_science_cluster: dsc, odh_dashboard_config: odh } = platform;

  return (
    <>
      <p className="maaspal-section-heading">Tenant Configuration</p>
      {!tenants.available ? (
        <MaasUnavailableNotice reason={tenants.reason} />
      ) : (
        <Table aria-label="MaaS tenants">
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th>Gateway</Th>
              <Th modifier="wrap">Max API key expiration</Th>
              <Th>Telemetry</Th>
              <Th>Phase</Th>
              <Th screenReaderText="Actions" />
            </Tr>
          </Thead>
          <Tbody>
            {tenants.items.length === 0 ? (
              <Tr>
                <Td colSpan={6} style={{ color: '#888', fontStyle: 'italic' }}>
                  No Tenant objects found.
                </Td>
              </Tr>
            ) : (
              tenants.items.map((tenant) => (
                <Tr key={`${tenant.namespace}/${tenant.name}`}>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}>{tenant.name}</Td>
                  <Td style={{ fontSize: '0.78rem' }}>
                    {tenant.gateway_ref ? `${tenant.gateway_ref.namespace}/${tenant.gateway_ref.name}` : '—'}
                  </Td>
                  <Td>{tenant.max_api_key_expiration_days ?? 'unlimited'}</Td>
                  <Td>
                    <Label isCompact color={tenant.telemetry_enabled ? 'green' : 'grey'}>
                      {tenant.telemetry_enabled ? 'Enabled' : 'Disabled'}
                    </Label>
                  </Td>
                  <Td>{tenant.phase ?? 'Unknown'}</Td>
                  <Td>
                    <Button
                      variant="link"
                      isInline
                      onClick={() =>
                        setYamlTarget({
                          title: `Tenant: ${tenant.name}`,
                          downloadFileName: `${tenant.name}.yaml`,
                          yamlText: tenant.raw_yaml,
                        })
                      }
                    >
                      View YAML
                    </Button>
                  </Td>
                </Tr>
              ))
            )}
          </Tbody>
        </Table>
      )}

      <p className="maaspal-section-heading" style={{ marginTop: '1.5rem' }}>DataScienceCluster</p>
      {!dsc.available ? (
        <MaasUnavailableNotice reason={dsc.reason} />
      ) : !dsc.item ? (
        <Card><CardBody style={{ color: '#888', fontStyle: 'italic' }}>No DataScienceCluster found.</CardBody></Card>
      ) : (
        <Card>
          <CardBody>
            <Label isCompact color={dsc.item.maas_management_state === 'Managed' ? 'green' : 'red'}>
              MaaS: {dsc.item.maas_management_state ?? 'not configured'}
            </Label>
            <div style={{ fontSize: '0.78rem', color: '#888', marginTop: '0.3rem', fontFamily: 'monospace' }}>
              {dsc.item.maas_field_path ?? 'no known MaaS field found on this DataScienceCluster'}
            </div>
            <Button
              variant="link"
              isInline
              style={{ marginTop: '0.4rem' }}
              onClick={() =>
                setYamlTarget({
                  title: `DataScienceCluster: ${dsc.item!.name}`,
                  downloadFileName: `${dsc.item!.name}.yaml`,
                  yamlText: dsc.item!.raw_yaml,
                })
              }
            >
              View YAML
            </Button>
          </CardBody>
        </Card>
      )}

      <p className="maaspal-section-heading" style={{ marginTop: '1.5rem' }}>OdhDashboardConfig</p>
      {!odh.available ? (
        <MaasUnavailableNotice reason={odh.reason} />
      ) : !odh.item ? (
        <Card><CardBody style={{ color: '#888', fontStyle: 'italic' }}>No OdhDashboardConfig found.</CardBody></Card>
      ) : (
        <Card>
          <CardBody>
            <FlagLabel label="Model as a Service" value={odh.item.model_as_service} />
            <FlagLabel label="External Models" value={odh.item.external_models} />
            <FlagLabel label="GenAI Studio" value={odh.item.gen_ai_studio} />
            <FlagLabel label="Observability Dashboard" value={odh.item.observability_dashboard} />
            <div>
              <Button
                variant="link"
                isInline
                onClick={() =>
                  setYamlTarget({
                    title: `OdhDashboardConfig: ${odh.item!.name}`,
                    downloadFileName: `${odh.item!.name}.yaml`,
                    yamlText: odh.item!.raw_yaml,
                  })
                }
              >
                View YAML
              </Button>
            </div>
          </CardBody>
        </Card>
      )}

      {yamlTarget !== null && (
        <Suspense fallback={<Spinner size="lg" aria-label="Loading editor" />}>
          <RawYamlModal
            title={yamlTarget.title}
            downloadFileName={yamlTarget.downloadFileName}
            yamlText={yamlTarget.yamlText}
            onClose={() => setYamlTarget(null)}
          />
        </Suspense>
      )}
    </>
  );
}
