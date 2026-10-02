import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Button, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import {
  getMaasLimitador,
  getMaasRateLimitPolicies,
  type MaasLimitador,
  type MaasTokenRateLimitPolicy,
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

export function RateLimitingTab() {
  const [policies, setPolicies] = useState<MaasTokenRateLimitPolicy[] | null>(null);
  const [policiesUnavailable, setPoliciesUnavailable] = useState<string | null | undefined>(undefined);
  const [limitadors, setLimitadors] = useState<MaasLimitador[] | null>(null);
  const [limitadorUnavailable, setLimitadorUnavailable] = useState<string | null | undefined>(undefined);
  const [yamlTarget, setYamlTarget] = useState<YamlTarget | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    const [policyResult, limitadorResult] = await Promise.all([
      getMaasRateLimitPolicies(),
      getMaasLimitador(),
    ]);
    if (policyResult.available) {
      setPolicies(policyResult.items);
      setPoliciesUnavailable(null);
    } else {
      setPolicies(null);
      setPoliciesUnavailable(policyResult.reason ?? 'unknown');
    }
    if (limitadorResult.available) {
      setLimitadors(limitadorResult.items);
      setLimitadorUnavailable(null);
    } else {
      setLimitadors(null);
      setLimitadorUnavailable(limitadorResult.reason ?? 'unknown');
    }
  }

  useEffect(() => {
    void fetchData();
    intervalRef.current = setInterval(() => { void fetchData(); }, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current);
    };
  }, []);

  if (policiesUnavailable === undefined) return <Spinner size="md" aria-label="Loading rate limiting" />;

  return (
    <>
      <p className="maaspal-section-heading">Token Rate Limit Policies</p>
      {policiesUnavailable ? (
        <MaasUnavailableNotice reason={policiesUnavailable} />
      ) : (
        <Table aria-label="MaaS token rate limit policies">
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th>Target</Th>
              <Th modifier="wrap">Limits</Th>
              <Th>Status</Th>
              <Th screenReaderText="Actions" />
            </Tr>
          </Thead>
          <Tbody>
            {!policies || policies.length === 0 ? (
              <Tr>
                <Td colSpan={5} style={{ color: '#888', fontStyle: 'italic' }}>
                  No TokenRateLimitPolicy objects found.
                </Td>
              </Tr>
            ) : (
              policies.map((policy) => (
                <Tr key={`${policy.namespace}/${policy.name}`}>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}>{policy.name}</Td>
                  <Td>
                    <Label isCompact>{policy.target_kind}</Label>
                    <div style={{ fontSize: '0.78rem', color: '#888', marginTop: '0.2rem' }}>
                      {policy.target_name}
                    </div>
                  </Td>
                  <Td>
                    {policy.limit_names.length === 0 ? (
                      <span style={{ color: '#888' }}>—</span>
                    ) : (
                      policy.limit_names.map((n) => (
                        <div key={n} style={{ fontSize: '0.75rem', fontFamily: 'monospace', color: '#555' }}>
                          {n}
                        </div>
                      ))
                    )}
                  </Td>
                  <Td>
                    <Label isCompact color={policy.accepted ? 'green' : 'grey'} style={{ marginRight: '0.3rem' }}>
                      {policy.accepted ? 'Accepted' : 'Not accepted'}
                    </Label>
                    <Label isCompact color={policy.enforced ? 'green' : 'orange'}>
                      {policy.enforced ? 'Enforced' : 'Not enforced'}
                    </Label>
                  </Td>
                  <Td>
                    <Button
                      variant="link"
                      isInline
                      onClick={() =>
                        setYamlTarget({
                          title: `TokenRateLimitPolicy: ${policy.name}`,
                          downloadFileName: `${policy.name}.yaml`,
                          yamlText: policy.raw_yaml,
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

      <p className="maaspal-section-heading" style={{ marginTop: '1.5rem' }}>Limitador</p>
      {limitadorUnavailable ? (
        <MaasUnavailableNotice reason={limitadorUnavailable} />
      ) : (
        <Table aria-label="MaaS Limitador">
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th modifier="wrap">Compiled limits</Th>
              <Th>Status</Th>
              <Th>Service</Th>
              <Th screenReaderText="Actions" />
            </Tr>
          </Thead>
          <Tbody>
            {!limitadors || limitadors.length === 0 ? (
              <Tr>
                <Td colSpan={5} style={{ color: '#888', fontStyle: 'italic' }}>
                  No Limitador object found.
                </Td>
              </Tr>
            ) : (
              limitadors.map((limitador) => (
                <Tr key={`${limitador.namespace}/${limitador.name}`}>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}>{limitador.name}</Td>
                  <Td>{limitador.limit_count}</Td>
                  <Td>
                    <Label isCompact color={limitador.ready ? 'green' : 'red'}>
                      {limitador.ready ? 'Ready' : 'Not ready'}
                    </Label>
                  </Td>
                  <Td style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>
                    {limitador.service_host ?? '—'}
                  </Td>
                  <Td>
                    <Button
                      variant="link"
                      isInline
                      onClick={() =>
                        setYamlTarget({
                          title: `Limitador: ${limitador.name}`,
                          downloadFileName: `${limitador.name}.yaml`,
                          yamlText: limitador.raw_yaml,
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
