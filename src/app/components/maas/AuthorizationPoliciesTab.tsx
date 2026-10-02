import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Button, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import { getMaasAuthPolicies, type MaasAuthPolicy } from '../../api/client';
import { MaasUnavailableNotice } from './MaasUnavailableNotice';

// Code-split: RawYamlModal pulls in Monaco (see monacoSetup.ts) — same
// reasoning as every other maas/ tab's lazy-loading, so Monaco's bundle is
// only fetched when a row's "View YAML" is actually clicked.
const RawYamlModal = lazy(() =>
  import('../RawYamlModal').then((m) => ({ default: m.RawYamlModal })),
);

const POLL_INTERVAL_MS = 25000;

// A MaaSAuthPolicy doesn't create the MaaSModelRef it references either (see
// SubscriptionsTab.tsx's identical ModelRefBadge) — so the reference can
// dangle without this policy's own status reflecting it.
function ModelRefBadge({ modelExists, modelReady }: { modelExists: boolean | null; modelReady: boolean | null }) {
  if (modelExists === false) {
    return (
      <Label isCompact color="red">
        ⚠ model not found
      </Label>
    );
  }
  if (modelExists === null) {
    return <span style={{ fontSize: '0.75rem', color: '#888' }}>model: unknown</span>;
  }
  if (modelReady === false) {
    return (
      <Label isCompact color="orange">
        ⚠ model not ready
      </Label>
    );
  }
  return null;
}

function ModelRefList({ policy }: { policy: MaasAuthPolicy }) {
  if (policy.model_refs.length === 0) return <span style={{ color: '#888' }}>—</span>;
  return (
    <>
      {policy.model_refs.map((m) => (
        <div key={`${m.namespace}/${m.name}`} style={{ marginBottom: '0.3rem' }}>
          <span style={{ fontWeight: 600 }}>{m.display_name}</span>{' '}
          <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#888' }}>
            ({m.namespace}/{m.name})
          </span>{' '}
          <ModelRefBadge modelExists={m.model_exists} modelReady={m.model_ready} />
        </div>
      ))}
    </>
  );
}

function SubjectChips({ owner }: { owner: MaasAuthPolicy['owner'] }) {
  const groups = owner.groups ?? [];
  const users = owner.users ?? [];
  if (groups.length === 0 && users.length === 0) return <span style={{ color: '#888' }}>—</span>;
  return (
    <>
      {groups.map((g) => (
        <Label key={`g-${g}`} isCompact color="blue" style={{ marginRight: '0.3rem', marginBottom: '0.2rem' }}>
          {g}
        </Label>
      ))}
      {users.map((u) => (
        <Label key={`u-${u}`} isCompact style={{ marginRight: '0.3rem', marginBottom: '0.2rem' }}>
          {u}
        </Label>
      ))}
    </>
  );
}

export function AuthorizationPoliciesTab() {
  const [items, setItems] = useState<MaasAuthPolicy[] | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<string | null | undefined>(undefined);
  const [yamlPolicy, setYamlPolicy] = useState<MaasAuthPolicy | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    const result = await getMaasAuthPolicies();
    if (result.available) {
      setItems(result.items);
      setUnavailableReason(null);
    } else {
      setItems(null);
      setUnavailableReason(result.reason ?? 'unknown');
    }
  }

  useEffect(() => {
    void fetchData();
    intervalRef.current = setInterval(() => { void fetchData(); }, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current);
    };
  }, []);

  if (unavailableReason === undefined) return <Spinner size="md" aria-label="Loading authorization policies" />;
  if (unavailableReason) return <MaasUnavailableNotice reason={unavailableReason} />;
  if (!items) return null;

  return (
    <>
      <Table aria-label="MaaS authorization policies">
        <Thead>
          <Tr>
            <Th>Name</Th>
            <Th>Phase</Th>
            <Th modifier="wrap">Target model(s)</Th>
            <Th>Granted to</Th>
            <Th screenReaderText="Actions" />
          </Tr>
        </Thead>
        <Tbody>
          {items.length === 0 ? (
            <Tr>
              <Td colSpan={5} style={{ color: '#888', fontStyle: 'italic' }}>
                No authorization policies found.
              </Td>
            </Tr>
          ) : (
            items.map((policy) => (
              <Tr key={`${policy.namespace}/${policy.name}`}>
                <Td>
                  <div style={{ fontWeight: 700 }}>{policy.display_name}</div>
                  <div style={{ fontSize: '0.78rem', color: '#888' }}>{policy.description}</div>
                </Td>
                <Td>
                  <Label isCompact color={policy.ready ? 'green' : 'grey'}>
                    {policy.phase ?? 'Unknown'}
                  </Label>
                </Td>
                <Td><ModelRefList policy={policy} /></Td>
                <Td><SubjectChips owner={policy.owner} /></Td>
                <Td>
                  <Button variant="link" isInline onClick={() => setYamlPolicy(policy)}>
                    View YAML
                  </Button>
                </Td>
              </Tr>
            ))
          )}
        </Tbody>
      </Table>

      {yamlPolicy !== null && (
        <Suspense fallback={<Spinner size="lg" aria-label="Loading editor" />}>
          <RawYamlModal
            title={`Auth Policy: ${yamlPolicy.display_name}`}
            downloadFileName={`${yamlPolicy.name}.yaml`}
            yamlText={yamlPolicy.raw_yaml}
            onClose={() => setYamlPolicy(null)}
          />
        </Suspense>
      )}
    </>
  );
}
