import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  CardBody,
  EmptyState,
  EmptyStateBody,
  Label,
  LabelGroup,
  Spinner,
  Stack,
  StackItem,
  Tooltip,
} from '@patternfly/react-core';
import { listScenarios, type Scenario } from '../api/client';
import { scenarioTitle } from '../scenarioTitles';

// Fixed display order for the built-in categories — mirrors
// harness/tests/test_scenarios.py's _KNOWN_CATEGORIES; keep both in sync.
// Organised around the question a user is asking, not the MaaS mechanism
// under test (ADR-025). "Custom" is always rendered last, and always rendered
// even when empty, so anyone dropping a scenario YAML in without a
// `category:` field (or with one we don't recognize) sees where it will land.
const CATEGORY_ORDER = [
  'Quick check',
  'Rate limits',
  'Access control',
  'API keys',
  'Usage metrics',
  'Performance',
  'Diagnostics',
] as const;
const CUSTOM_CATEGORY = 'Custom';

const CATEGORY_BLURB: Record<string, string> = {
  'Quick check': 'Start here — is MaaS working, and does my subscription work?',
  'Rate limits': 'Are token limits enforced the way my subscriptions say?',
  'Access control': 'Is access denied when it should be?',
  'API keys': 'Do keys behave correctly from creation to revocation?',
  'Usage metrics': 'Do MaaS’s own counters match real traffic?',
  Performance: 'Latency, throughput and errors under load.',
  Diagnostics: 'Read-only checks of how models are wired up.',
};

// Plain-language names for the `mutates:` values a scenario declares.
const MUTATES_LABEL: Record<string, string> = {
  api_keys: 'API keys',
  subscriptions: 'subscriptions',
  auth_policies: 'auth policies',
  models: 'models',
  service_accounts: 'ServiceAccount identities',
  routes: 'routes',
};

function groupByCategory(scenarios: Scenario[]): Map<string, Scenario[]> {
  const groups = new Map<string, Scenario[]>();
  for (const category of CATEGORY_ORDER) groups.set(category, []);
  groups.set(CUSTOM_CATEGORY, []);

  for (const s of scenarios) {
    const key = groups.has(s.category) ? s.category : CUSTOM_CATEGORY;
    groups.get(key)!.push(s);
  }
  // Within a category, scenarios that check the user's own setup come before
  // ones that build temporary resources to probe MaaS itself; then `order:`.
  for (const items of groups.values()) {
    items.sort(
      (a, b) =>
        Number(a.kind === 'explore') - Number(b.kind === 'explore') || (a.order ?? 100) - (b.order ?? 100),
    );
  }
  return groups;
}

function describeMutations(s: Scenario): string {
  const what = (s.mutates ?? []).map((m) => MUTATES_LABEL[m] ?? m);
  if (what.length === 0) return 'Read-only: creates and changes nothing.';
  return `Creates temporary ${what.join(', ')} — removed again when the run finishes (auto cleanup).`;
}

export function ScenarioBadges({ scenario }: { scenario: Scenario }) {
  const explore = scenario.kind === 'explore';
  return (
    <LabelGroup numLabels={4}>
      <Tooltip content={describeMutations(scenario)}>
        <Label color={explore ? 'orange' : 'blue'} isCompact>
          {explore ? 'Creates temporary resources' : (scenario.mutates ?? []).length ? 'Uses your setup' : 'Read-only'}
        </Label>
      </Tooltip>
      {(scenario.needs_rbac ?? []).length > 0 && (
        <Tooltip
          content={`Needs extra permissions applied first: ${(scenario.needs_rbac ?? [])
            .map((r) => `deploy/rbac-${r}.yaml`)
            .join(', ')}`}
        >
          <Label color="red" variant="outline" isCompact>
            Needs extra RBAC
          </Label>
        </Tooltip>
      )}
      {scenario.est_duration && (
        <Label color="grey" variant="outline" isCompact>
          {scenario.est_duration}
        </Label>
      )}
    </LabelGroup>
  );
}

interface Props {
  onRun: (scenario: Scenario) => void;
}

function ScenarioCard({ scenario, onRun }: { scenario: Scenario; onRun: (s: Scenario) => void }) {
  return (
    <Card>
      <CardBody style={{ padding: '0.75rem 1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.5rem' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="maaspal-scenario-name" style={{ marginBottom: '0.25rem' }}>
              {scenarioTitle(scenario)}
            </div>
            <p style={{ margin: '0 0 0.4rem', color: '#666', fontSize: '0.8rem', lineHeight: 1.4 }}>
              {scenario.summary || scenario.description}
            </p>
            <ScenarioBadges scenario={scenario} />
          </div>
          <Button
            variant="primary"
            onClick={() => onRun(scenario)}
            style={{ flexShrink: 0, alignSelf: 'center' }}
            aria-label={`Run ${scenarioTitle(scenario)}`}
          >
            Run
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

export function ScenarioList({ onRun }: Props) {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listScenarios()
      .then(setScenarios)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load scenarios'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <Spinner aria-label="Loading scenarios" />;

  if (error) {
    return (
      <Alert
        variant="danger"
        title="Could not load scenarios"
        actionLinks={
          <Button variant="link" onClick={load}>Retry</Button>
        }
      >
        {error}
      </Alert>
    );
  }

  if (scenarios.length === 0) {
    return (
      <EmptyState>
        <EmptyStateBody>No scenarios available.</EmptyStateBody>
      </EmptyState>
    );
  }

  const groups = groupByCategory(scenarios);

  return (
    <Stack hasGutter>
      {[...CATEGORY_ORDER, CUSTOM_CATEGORY].map((category) => {
        const items = groups.get(category) ?? [];
        if (items.length === 0 && category !== CUSTOM_CATEGORY) return null;

        return (
          <StackItem key={category}>
            <p className="maaspal-section-heading" style={{ marginBottom: '0.15rem' }}>
              {category}
            </p>
            {CATEGORY_BLURB[category] && (
              <p style={{ margin: '0 0 0.5rem', color: '#888', fontSize: '0.78rem' }}>
                {CATEGORY_BLURB[category]}
              </p>
            )}
            {items.length === 0 ? (
              <p style={{ color: '#888', fontSize: '0.8rem', margin: 0 }}>
                No custom scenarios yet — add a scenario YAML under <code>scenarios/</code> with
                no <code>category:</code> field (or one of your own) and it will show up here.
              </p>
            ) : (
              <Stack hasGutter>
                {items.map((s) => (
                  <StackItem key={s.name} className="maaspal-scenario-card">
                    <ScenarioCard scenario={s} onRun={onRun} />
                  </StackItem>
                ))}
              </Stack>
            )}
          </StackItem>
        );
      })}
    </Stack>
  );
}
