import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Button, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import { getMaasSubscriptions, type MaasSubscription } from '../../api/client';
import { MaasUnavailableNotice } from './MaasUnavailableNotice';

// Code-split: RawYamlModal pulls in Monaco (see monacoSetup.ts) — same
// reasoning as RunDetail.tsx's lazy-loading of RunSettingsModal, so Monaco's
// bundle is only fetched when a row's "View YAML" is actually clicked, not on
// every visit to the MaaS Setup tab.
const RawYamlModal = lazy(() =>
  import('../RawYamlModal').then((m) => ({ default: m.RawYamlModal })),
);

const POLL_INTERVAL_MS = 25000;

// Mirrors ModelsTab.tsx's AuthPolicyBadge — a MaaSSubscription only grants
// quota, never gateway access (see api/maas_client.py:list_subscriptions), so
// a missing auth policy here means this subscription's owners have quota but
// can't actually reach the model. Kept local rather than shared/imported
// since each maas/ tab is otherwise self-contained.
function AuthPolicyBadge({ hasAuthPolicy }: { hasAuthPolicy: boolean | null }) {
  if (hasAuthPolicy === null) {
    return <span style={{ fontSize: '0.75rem', color: '#888' }}>auth policy: unknown</span>;
  }
  return (
    <Label isCompact color={hasAuthPolicy ? 'green' : 'orange'}>
      {hasAuthPolicy ? '✓ has auth policy' : '⚠ no auth policy'}
    </Label>
  );
}

// A subscription doesn't create the MaaSModelRef it references (that's done
// by publishing a model, see Catalog item B) — so the reference can dangle
// (renamed/deleted model) without this subscription's own status reflecting
// it. Surfaced here rather than silently falling back to the raw name.
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

function ModelRefCoverageList({ sub }: { sub: MaasSubscription }) {
  if (sub.model_refs.length === 0) return <span style={{ color: '#888' }}>—</span>;
  return (
    <>
      {sub.model_refs.map((m) => (
        <div key={`${m.namespace}/${m.name}`} style={{ marginBottom: '0.5rem' }}>
          <div>
            <span style={{ fontWeight: 600 }}>{m.display_name}</span>{' '}
            <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#888' }}>
              ({m.namespace}/{m.name})
            </span>
          </div>
          <div style={{ marginTop: '0.15rem' }}>
            {m.token_rate_limits.map((rl, i) => (
              <Label key={i} isCompact style={{ marginRight: '0.3rem' }}>
                {rl.limit} / {rl.window}
              </Label>
            ))}
            <ModelRefBadge modelExists={m.model_exists} modelReady={m.model_ready} />{' '}
            <AuthPolicyBadge hasAuthPolicy={m.has_auth_policy} />
          </div>
        </div>
      ))}
    </>
  );
}

function OwnerChips({ owner }: { owner: MaasSubscription['owner'] }) {
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

export function SubscriptionsTab() {
  const [items, setItems] = useState<MaasSubscription[] | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<string | null | undefined>(undefined);
  const [yamlSub, setYamlSub] = useState<MaasSubscription | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    const result = await getMaasSubscriptions();
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

  if (unavailableReason === undefined) return <Spinner size="md" aria-label="Loading subscriptions" />;
  if (unavailableReason) return <MaasUnavailableNotice reason={unavailableReason} />;
  if (!items) return null;

  return (
    <>
      <Table aria-label="MaaS subscriptions">
        <Thead>
          <Tr>
            <Th>Name</Th>
            <Th>Priority</Th>
            <Th>Phase</Th>
            <Th modifier="wrap">Model refs (rate limit, ref status, auth policy)</Th>
            <Th>Granted to</Th>
            <Th screenReaderText="Actions" />
          </Tr>
        </Thead>
        <Tbody>
          {items.length === 0 ? (
            <Tr>
              <Td colSpan={6} style={{ color: '#888', fontStyle: 'italic' }}>
                No subscriptions found.
              </Td>
            </Tr>
          ) : (
            items.map((sub) => (
              <Tr key={`${sub.namespace}/${sub.name}`}>
                <Td>
                  <div style={{ fontWeight: 700 }}>{sub.display_name}</div>
                  <div style={{ fontSize: '0.78rem', color: '#888' }}>{sub.description}</div>
                  {sub.priority_conflict && (
                    <Label isCompact color="orange" style={{ marginTop: '0.2rem' }}>
                      ⚠ priority conflict
                    </Label>
                  )}
                </Td>
                <Td>{sub.priority ?? '—'}</Td>
                <Td>
                  <Label isCompact color={sub.ready ? 'green' : 'grey'}>
                    {sub.phase ?? 'Unknown'}
                  </Label>
                </Td>
                <Td><ModelRefCoverageList sub={sub} /></Td>
                <Td><OwnerChips owner={sub.owner} /></Td>
                <Td>
                  <Button variant="link" isInline onClick={() => setYamlSub(sub)}>
                    View YAML
                  </Button>
                </Td>
              </Tr>
            ))
          )}
        </Tbody>
      </Table>

      {yamlSub !== null && (
        <Suspense fallback={<Spinner size="lg" aria-label="Loading editor" />}>
          <RawYamlModal
            title={`Subscription: ${yamlSub.display_name}`}
            downloadFileName={`${yamlSub.name}.yaml`}
            yamlText={yamlSub.raw_yaml}
            onClose={() => setYamlSub(null)}
          />
        </Suspense>
      )}
    </>
  );
}
