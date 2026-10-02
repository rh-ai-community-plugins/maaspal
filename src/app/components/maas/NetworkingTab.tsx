import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Button, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import {
  getMaasGateways,
  getMaasHttpRoutes,
  type MaasGateway,
  type MaasHttpRoute,
} from '../../api/client';
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

export function NetworkingTab() {
  const [gateways, setGateways] = useState<MaasGateway[] | null>(null);
  const [gatewaysUnavailable, setGatewaysUnavailable] = useState<string | null | undefined>(undefined);
  const [routes, setRoutes] = useState<MaasHttpRoute[] | null>(null);
  const [routesUnavailable, setRoutesUnavailable] = useState<string | null | undefined>(undefined);
  const [yamlTarget, setYamlTarget] = useState<YamlTarget | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    const [gatewayResult, routeResult] = await Promise.all([getMaasGateways(), getMaasHttpRoutes()]);
    if (gatewayResult.available) {
      setGateways(gatewayResult.items);
      setGatewaysUnavailable(null);
    } else {
      setGateways(null);
      setGatewaysUnavailable(gatewayResult.reason ?? 'unknown');
    }
    if (routeResult.available) {
      setRoutes(routeResult.items);
      setRoutesUnavailable(null);
    } else {
      setRoutes(null);
      setRoutesUnavailable(routeResult.reason ?? 'unknown');
    }
  }

  useEffect(() => {
    void fetchData();
    intervalRef.current = setInterval(() => { void fetchData(); }, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current);
    };
  }, []);

  if (gatewaysUnavailable === undefined) return <Spinner size="md" aria-label="Loading networking" />;

  return (
    <>
      <p className="maaspal-section-heading">Gateways</p>
      {gatewaysUnavailable ? (
        <MaasUnavailableNotice reason={gatewaysUnavailable} />
      ) : (
        <Table aria-label="MaaS gateways">
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th>Class</Th>
              <Th>Address</Th>
              <Th>Status</Th>
              <Th screenReaderText="Actions" />
            </Tr>
          </Thead>
          <Tbody>
            {!gateways || gateways.length === 0 ? (
              <Tr>
                <Td colSpan={5} style={{ color: '#888', fontStyle: 'italic' }}>
                  No Gateway objects found.
                </Td>
              </Tr>
            ) : (
              gateways.map((gw) => (
                <Tr key={`${gw.namespace}/${gw.name}`}>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}>{gw.name}</Td>
                  <Td>{gw.gateway_class ?? '—'}</Td>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>{gw.address ?? '—'}</Td>
                  <Td>
                    <Label isCompact color={gw.programmed ? 'green' : 'red'}>
                      {gw.programmed ? 'Programmed' : 'Not programmed'}
                    </Label>
                  </Td>
                  <Td>
                    <Button
                      variant="link"
                      isInline
                      onClick={() =>
                        setYamlTarget({
                          title: `Gateway: ${gw.name}`,
                          downloadFileName: `${gw.name}.yaml`,
                          yamlText: gw.raw_yaml,
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

      <p className="maaspal-section-heading" style={{ marginTop: '1.5rem' }}>HTTP Routes</p>
      {routesUnavailable ? (
        <MaasUnavailableNotice reason={routesUnavailable} />
      ) : (
        <Table aria-label="MaaS HTTP routes">
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th>Namespace</Th>
              <Th>Parent gateway</Th>
              <Th>Owning model</Th>
              <Th screenReaderText="Actions" />
            </Tr>
          </Thead>
          <Tbody>
            {!routes || routes.length === 0 ? (
              <Tr>
                <Td colSpan={5} style={{ color: '#888', fontStyle: 'italic' }}>
                  No HTTPRoute objects found.
                </Td>
              </Tr>
            ) : (
              routes.map((route) => (
                <Tr key={`${route.namespace}/${route.name}`}>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}>{route.name}</Td>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{route.namespace}</Td>
                  <Td>
                    {route.parent_gateway ? (
                      <>
                        <span style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{route.parent_gateway}</span>
                        <div style={{ fontSize: '0.72rem', color: '#888' }}>{route.parent_gateway_namespace}</div>
                      </>
                    ) : (
                      <span style={{ color: '#888' }}>—</span>
                    )}
                  </Td>
                  <Td>
                    {route.owning_model ? (
                      <Label isCompact color="blue">{route.owning_model}</Label>
                    ) : (
                      <span style={{ color: '#888' }}>—</span>
                    )}
                  </Td>
                  <Td>
                    <Button
                      variant="link"
                      isInline
                      onClick={() =>
                        setYamlTarget({
                          title: `HTTPRoute: ${route.name}`,
                          downloadFileName: `${route.name}.yaml`,
                          yamlText: route.raw_yaml,
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
