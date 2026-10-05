import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Button, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import { getMaasModels, type MaasModel } from '../../api/client';
import { MaasUnavailableNotice } from './MaasUnavailableNotice';

// Code-split: RawYamlModal pulls in Monaco (see monacoSetup.ts) — same
// reasoning as RunDetail.tsx's lazy-loading of RunSettingsModal, so Monaco's
// bundle is only fetched when a row's "View YAML" is actually clicked, not on
// every visit to the MaaS Setup tab.
const RawYamlModal = lazy(() =>
  import('../RawYamlModal').then((m) => ({ default: m.RawYamlModal })),
);

const POLL_INTERVAL_MS = 25000;

interface YamlTarget {
  title: string;
  downloadFileName: string;
  yamlText: string;
}

function SubscriptionChips({ subs }: { subs: MaasModel['subscriptions'] }) {
  if (subs.length === 0) return <span style={{ color: '#888' }}>—</span>;
  return (
    <>
      {subs.map((s) => (
        <Label key={s.name} isCompact color="blue" style={{ marginRight: '0.3rem', marginBottom: '0.2rem' }}>
          {s.display_name ?? s.name}
        </Label>
      ))}
    </>
  );
}

// `null` means "couldn't tell" (the SA lacks RBAC for auth policies) — shown
// as a muted note, never collapsed into the "missing" (false) case, which
// reads as an actual misconfiguration to fix. Its own column, not tucked
// under Subscriptions — quota (subscriptions) and gateway access (auth
// policy) are two different governance questions, per Catalog item B/C.
// When at least one policy actually covers this model, its status label is
// followed by an explicit "View YAML" link — not a clickable label on its
// own, which looked identical to every other (non-clickable) status label
// in this table and wasn't discoverable as an action.
function AuthPolicyCell({
  hasAuthPolicy,
  policies,
  onSelect,
}: {
  hasAuthPolicy: boolean | null;
  policies: MaasModel['auth_policies'];
  onSelect: (target: YamlTarget) => void;
}) {
  if (hasAuthPolicy === null) {
    return <span style={{ fontSize: '0.75rem', color: '#888' }}>unknown</span>;
  }
  if (!hasAuthPolicy || policies.length === 0) {
    return <Label isCompact color="orange">⚠ no auth policy</Label>;
  }
  return (
    <>
      {policies.map((p) => (
        <div key={p.name} style={{ marginBottom: '0.3rem' }}>
          <Label isCompact color={p.ready ? 'green' : 'grey'} style={{ marginRight: '0.4rem' }}>
            {p.ready ? '✓' : '⚠'} {p.display_name}
          </Label>
          <Button
            variant="link"
            isInline
            style={{ fontSize: '0.75rem' }}
            onClick={() =>
              onSelect({
                title: `Auth Policy: ${p.display_name}`,
                downloadFileName: `${p.name}.yaml`,
                yamlText: p.raw_yaml,
              })
            }
          >
            View YAML
          </Button>
        </div>
      ))}
    </>
  );
}

// The namespace and its `gateway-access` label are one fact (the label
// lives ON the namespace), so they share a column — separate from Hosting,
// which is about how the model itself is served.
function NamespaceCell({ model }: { model: MaasModel }) {
  return (
    <div>
      <div style={{ fontSize: '0.8rem', fontFamily: 'monospace', color: '#333' }}>
        {model.namespace}
      </div>
      {model.gateway_access_label === false && (
        <Label isCompact color="red" style={{ marginTop: '0.3rem' }}>
          ⚠ missing gateway-access label
        </Label>
      )}
      {model.gateway_access_label === true && (
        <Label isCompact color="green" style={{ marginTop: '0.3rem' }}>
          ✓ gateway-access
        </Label>
      )}
      {model.gateway_access_label === null && (
        <div style={{ fontSize: '0.75rem', color: '#888', marginTop: '0.3rem' }}>gateway-access: unknown</div>
      )}
    </div>
  );
}

// Answers "is this model served by OpenShift AI itself, or routed out to a
// third-party provider?" — the badge itself always reads Internal/External;
// the actual backing kind (LLMInferenceService, ExternalModel, ...) is shown
// underneath so the distinction is never ambiguous even for a future kind
// this UI doesn't special-case yet.
function HostingBadge({ model }: { model: MaasModel }) {
  const isExternal = model.hosting === 'external';
  return (
    <div>
      <Label isCompact color={isExternal ? 'purple' : 'blue'}>
        {isExternal ? 'External' : 'Internal'}
      </Label>
      <div style={{ fontSize: '0.72rem', color: '#888', marginTop: '0.3rem' }}>
        {model.kind ?? 'Unknown kind'}
      </div>
      {typeof model.serving?.replicas === 'number' && (
        <div style={{ fontSize: '0.72rem', color: '#888' }}>
          {model.serving.replicas} replica{model.serving.replicas === 1 ? '' : 's'}
        </div>
      )}
    </div>
  );
}

// Own column, not nested under Hosting — an external model's provider(s) and
// whether their credential Secret is correctly labeled (RHOAI 3.5+; see
// docs/architecture/maas-domain-reference.md Catalog item B) is a distinct
// question from how the model is served. Each provider's own "View YAML"
// link lives right here, next to that specific provider — not lumped into
// the model's own Actions column — since it opens a different CR (the
// resolved ExternalProvider, not the ExternalModel).
function ExternalProviderCell({
  providers,
  onSelect,
}: {
  providers: MaasModel['external_providers'];
  onSelect: (target: YamlTarget) => void;
}) {
  if (providers.length === 0) return <span style={{ color: '#888' }}>—</span>;
  return (
    <>
      {providers.map((p, i) => (
        <div key={`${p.provider_name ?? 'unknown'}-${i}`} style={{ marginBottom: '0.3rem' }}>
          <div style={{ fontSize: '0.8rem' }}>
            <strong>{p.provider_name ?? 'unknown provider'}</strong>
            {p.target_model && <span style={{ color: '#888' }}> → {p.target_model}</span>}
          </div>
          {p.credential_secret_name ? (
            <Label
              isCompact
              color={p.credential_secret_label_ok ? 'green' : p.credential_secret_label_ok === false ? 'red' : 'grey'}
              style={{ marginTop: '0.2rem' }}
            >
              {p.credential_secret_label_ok === true && '✓ credential secret labeled'}
              {p.credential_secret_label_ok === false && '⚠ credential secret missing label'}
              {p.credential_secret_label_ok === null && 'credential secret: unknown'}
            </Label>
          ) : (
            <span style={{ fontSize: '0.72rem', color: '#888' }}>no credential secret configured</span>
          )}
          {p.raw_yaml !== null && (
            <div>
              <Button
                variant="link"
                isInline
                style={{ fontSize: '0.75rem' }}
                onClick={() =>
                  onSelect({
                    title: `Provider: ${p.provider_name ?? 'unknown provider'}`,
                    downloadFileName: `${p.provider_name ?? 'provider'}.yaml`,
                    yamlText: p.raw_yaml as string,
                  })
                }
              >
                View YAML
              </Button>
            </div>
          )}
        </div>
      ))}
    </>
  );
}

export function ModelsTab() {
  const [items, setItems] = useState<MaasModel[] | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<string | null | undefined>(undefined);
  const [yamlTarget, setYamlTarget] = useState<YamlTarget | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    const result = await getMaasModels();
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

  if (unavailableReason === undefined) return <Spinner size="md" aria-label="Loading models" />;
  if (unavailableReason) return <MaasUnavailableNotice reason={unavailableReason} />;
  if (!items) return null;

  return (
    <>
      <Table aria-label="MaaS models">
        <Thead>
          <Tr>
            <Th>Name</Th>
            <Th>Namespace</Th>
            <Th>Hosting</Th>
            <Th>External provider</Th>
            <Th>Status</Th>
            <Th>Endpoint</Th>
            <Th>Subscriptions</Th>
            <Th>Auth policy</Th>
            <Th screenReaderText="Actions" />
          </Tr>
        </Thead>
        <Tbody>
          {items.length === 0 ? (
            <Tr>
              <Td colSpan={9} style={{ color: '#888', fontStyle: 'italic' }}>
                No models found.
              </Td>
            </Tr>
          ) : (
            items.map((model) => (
              <Tr key={`${model.namespace}/${model.name}`}>
                <Td>
                  <div style={{ fontWeight: 700 }}>{model.display_name}</div>
                  <div style={{ fontSize: '0.78rem', color: '#888' }}>{model.description}</div>
                </Td>
                <Td><NamespaceCell model={model} /></Td>
                <Td><HostingBadge model={model} /></Td>
                <Td><ExternalProviderCell providers={model.external_providers} onSelect={setYamlTarget} /></Td>
                <Td>
                  <Label isCompact color={model.ready ? 'green' : 'red'}>
                    {model.phase ?? (model.ready ? 'Ready' : 'Not ready')}
                  </Label>
                </Td>
                <Td>
                  {model.endpoint ? (
                    <span style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>{model.endpoint}</span>
                  ) : (
                    <span style={{ color: '#888' }}>—</span>
                  )}
                </Td>
                <Td><SubscriptionChips subs={model.subscriptions} /></Td>
                <Td>
                  <AuthPolicyCell
                    hasAuthPolicy={model.has_auth_policy}
                    policies={model.auth_policies}
                    onSelect={setYamlTarget}
                  />
                </Td>
                <Td>
                  <div>
                    <Button
                      variant="link"
                      isInline
                      onClick={() =>
                        setYamlTarget({
                          title: `Model: ${model.display_name}`,
                          downloadFileName: `${model.name}.yaml`,
                          yamlText: model.raw_yaml,
                        })
                      }
                    >
                      View YAML
                    </Button>
                  </div>
                  {model.serving_raw_yaml !== null && (
                    <div>
                      <Button
                        variant="link"
                        isInline
                        style={{ fontSize: '0.75rem' }}
                        onClick={() =>
                          setYamlTarget({
                            title: `Deployment: ${model.display_name}`,
                            downloadFileName: `${model.name}-deployment.yaml`,
                            yamlText: model.serving_raw_yaml as string,
                          })
                        }
                      >
                        View Deployment YAML
                      </Button>
                    </div>
                  )}
                </Td>
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
