import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Button, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import { getMaasAccess, type MaasAccessRow } from '../../api/client';
import { MaasUnavailableNotice } from './MaasUnavailableNotice';

// Code-split: RawYamlModal pulls in Monaco (see monacoSetup.ts) — same
// reasoning as the Subscriptions/Models tabs' lazy-loading, so Monaco's
// bundle is only fetched when a chip's YAML is actually opened.
const RawYamlModal = lazy(() =>
  import('../RawYamlModal').then((m) => ({ default: m.RawYamlModal })),
);

const POLL_INTERVAL_MS = 25000;

interface YamlTarget {
  title: string;
  downloadFileName: string;
  yamlText: string;
}

function UserChips({ users }: { users: string[] | null }) {
  if (users === null) {
    return <span style={{ color: '#888', fontStyle: 'italic' }}>unknown (Groups unavailable)</span>;
  }
  if (users.length === 0) return <span style={{ color: '#888' }}>no members</span>;
  return (
    <>
      {users.map((u) => (
        <Label key={u} isCompact style={{ marginRight: '0.3rem', marginBottom: '0.2rem' }}>
          {u}
        </Label>
      ))}
    </>
  );
}

// Each chip opens the underlying CR's YAML — clicking a subscription/auth
// policy is how you find out *why* a group ended up with a mismatch (e.g. a
// modelRef typo that means it silently doesn't match the other side).
function SubscriptionChips({
  subs,
  onSelect,
}: {
  subs: MaasAccessRow['subscriptions'];
  onSelect: (target: YamlTarget) => void;
}) {
  if (subs.length === 0) return <span style={{ color: '#888' }}>—</span>;
  return (
    <>
      {subs.map((s) => (
        <Label
          key={s.name}
          isCompact
          color="blue"
          style={{ marginRight: '0.3rem', marginBottom: '0.2rem', cursor: 'pointer' }}
          onClick={() =>
            onSelect({
              title: `Subscription: ${s.display_name}`,
              downloadFileName: `${s.name}.yaml`,
              yamlText: s.raw_yaml,
            })
          }
        >
          {s.display_name} (p{s.priority ?? '—'})
        </Label>
      ))}
    </>
  );
}

function AuthPolicyChips({
  policies,
  onSelect,
}: {
  policies: MaasAccessRow['auth_policies'];
  onSelect: (target: YamlTarget) => void;
}) {
  if (policies.length === 0) return <span style={{ color: '#888' }}>—</span>;
  return (
    <>
      {policies.map((p) => (
        <Label
          key={p.name}
          isCompact
          color={p.ready ? 'green' : 'grey'}
          style={{ marginRight: '0.3rem', marginBottom: '0.2rem', cursor: 'pointer' }}
          onClick={() =>
            onSelect({
              title: `Auth Policy: ${p.display_name}`,
              downloadFileName: `${p.name}.yaml`,
              yamlText: p.raw_yaml,
            })
          }
        >
          {p.display_name}
        </Label>
      ))}
    </>
  );
}

function MismatchChips({ row }: { row: MaasAccessRow }) {
  if (row.quota_without_access.length === 0 && row.access_without_quota.length === 0) {
    return <span style={{ color: '#888' }}>—</span>;
  }
  return (
    <>
      {row.quota_without_access.map((m) => (
        <Label key={`qwa-${m}`} isCompact color="orange" style={{ marginRight: '0.3rem', marginBottom: '0.2rem' }}>
          ⚠ quota, no gateway access: {m}
        </Label>
      ))}
      {row.access_without_quota.map((m) => (
        <Label key={`awq-${m}`} isCompact color="orange" style={{ marginRight: '0.3rem', marginBottom: '0.2rem' }}>
          ⚠ gateway access, no quota: {m}
        </Label>
      ))}
    </>
  );
}

export function AccessControlTab() {
  const [items, setItems] = useState<MaasAccessRow[] | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<string | null | undefined>(undefined);
  const [yamlTarget, setYamlTarget] = useState<YamlTarget | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    const result = await getMaasAccess();
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

  if (unavailableReason === undefined) return <Spinner size="md" aria-label="Loading access control" />;
  if (unavailableReason) return <MaasUnavailableNotice reason={unavailableReason} />;
  if (!items) return null;

  return (
    <>
      <Table aria-label="MaaS access control">
        <Thead>
          <Tr>
            <Th>Group</Th>
            <Th>Members</Th>
            <Th modifier="wrap">Quota (subscriptions)</Th>
            <Th modifier="wrap">Gateway access (auth policies)</Th>
            <Th modifier="wrap">Mismatches</Th>
          </Tr>
        </Thead>
        <Tbody>
          {items.length === 0 ? (
            <Tr>
              <Td colSpan={5} style={{ color: '#888', fontStyle: 'italic' }}>
                No groups reference a subscription or auth policy.
              </Td>
            </Tr>
          ) : (
            items.map((row) => (
              <Tr key={row.name}>
                <Td style={{ fontWeight: 700 }}>
                  {row.name}
                  {row.raw_yaml !== null && (
                    <div>
                      <Button
                        variant="link"
                        isInline
                        style={{ fontWeight: 400, fontSize: '0.78rem' }}
                        onClick={() =>
                          setYamlTarget({
                            title: `Group: ${row.name}`,
                            downloadFileName: `${row.name}.yaml`,
                            yamlText: row.raw_yaml as string,
                          })
                        }
                      >
                        View YAML
                      </Button>
                    </div>
                  )}
                </Td>
                <Td><UserChips users={row.users} /></Td>
                <Td><SubscriptionChips subs={row.subscriptions} onSelect={setYamlTarget} /></Td>
                <Td><AuthPolicyChips policies={row.auth_policies} onSelect={setYamlTarget} /></Td>
                <Td><MismatchChips row={row} /></Td>
              </Tr>
            ))
          )}
        </Tbody>
      </Table>

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
