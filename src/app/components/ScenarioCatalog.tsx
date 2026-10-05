import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  CardTitle,
  Checkbox,
  EmptyState,
  EmptyStateActions,
  EmptyStateBody,
  EmptyStateFooter,
  Gallery,
  Label,
  LabelGroup,
  SearchInput,
  Spinner,
  Tooltip,
} from '@patternfly/react-core';
import { listScenarios, type Scenario } from '../api/client';
import { scenarioTitle } from '../scenarioTitles';

// Fixed display order for the built-in categories — mirrors KNOWN_CATEGORIES in
// bff/api/routes/scenarios.py; keep both in sync. Organised around the
// question a user is asking, not the MaaS mechanism under test (ADR-025).
// "Custom" holds scenarios someone added without one of these categories; it
// comes first, since a team's own scenarios are what they look for most.
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
  [CUSTOM_CATEGORY]: 'Scenarios added for this cluster.',
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

type Source = 'custom' | 'builtin';
type ScenarioType = 'setup' | 'temporary' | 'readonly';

const SOURCE_LABEL: Record<Source, string> = { custom: 'Custom', builtin: 'Built-in' };
const TYPE_LABEL: Record<ScenarioType, string> = {
  setup: 'Uses your setup',
  temporary: 'Creates temporary resources',
  readonly: 'Read-only',
};

function scenarioType(s: Scenario): ScenarioType {
  if (s.kind === 'explore') return 'temporary';
  return (s.mutates ?? []).length ? 'setup' : 'readonly';
}

function scenarioSource(s: Scenario): Source {
  return s.custom ? 'custom' : 'builtin';
}

function knownCategory(s: Scenario): string {
  return (CATEGORY_ORDER as readonly string[]).includes(s.category) ? s.category : CUSTOM_CATEGORY;
}

// Custom first, then scenarios that check the user's own setup before ones
// that build temporary resources to probe MaaS itself, then `order:`.
function compareScenarios(a: Scenario, b: Scenario): number {
  return (
    Number(!a.custom) - Number(!b.custom) ||
    Number(a.kind === 'explore') - Number(b.kind === 'explore') ||
    (a.order ?? 100) - (b.order ?? 100) ||
    scenarioTitle(a).localeCompare(scenarioTitle(b))
  );
}

function groupByCategory(scenarios: Scenario[]): [string, Scenario[]][] {
  return [CUSTOM_CATEGORY, ...CATEGORY_ORDER]
    .map((category): [string, Scenario[]] => [
      category,
      scenarios.filter((s) => knownCategory(s) === category).sort(compareScenarios),
    ])
    .filter(([, items]) => items.length > 0);
}

function describeMutations(s: Scenario): string {
  const what = (s.mutates ?? []).map((m) => MUTATES_LABEL[m] ?? m);
  if (what.length === 0) return 'Read-only: creates and changes nothing.';
  return `Creates temporary ${what.join(', ')} — removed again when the run finishes (auto cleanup).`;
}

// needs_rbac ids ("model-write") → the chart value that grants them
// ("rbac.modelWrite=true").
function rbacChartValue(id: string): string {
  return `rbac.${id.replace(/-([a-z])/g, (_m, c: string) => c.toUpperCase())}=true`;
}

export function ScenarioBadges({ scenario }: { scenario: Scenario }) {
  const type = scenarioType(scenario);
  return (
    <LabelGroup numLabels={5}>
      {scenario.custom && (
        <Tooltip content="Added for this cluster — not one of the scenarios MaaS:PAL ships with.">
          <Label color="purple" isCompact>
            Custom
          </Label>
        </Tooltip>
      )}
      <Tooltip content={describeMutations(scenario)}>
        <Label color={type === 'temporary' ? 'orange' : 'blue'} isCompact>
          {TYPE_LABEL[type]}
        </Label>
      </Tooltip>
      {(scenario.needs_rbac ?? []).length > 0 && (
        <Tooltip
          content={`Needs extra permissions on the MaaS:PAL service account (Helm chart values): ${(
            scenario.needs_rbac ?? []
          )
            .map(rbacChartValue)
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

function ScenarioCard({ scenario, onRun }: { scenario: Scenario; onRun: (s: Scenario) => void }) {
  const title = scenarioTitle(scenario);
  return (
    <Card
      className="maaspal-catalog-card"
      isCompact
      isFullHeight
      onClick={() => onRun(scenario)}
      data-custom={scenario.custom ? 'true' : undefined}
    >
      <CardHeader>
        <CardTitle className="maaspal-scenario-name">{title}</CardTitle>
      </CardHeader>
      <CardBody>
        <Tooltip content={scenario.description} isContentLeftAligned>
          <p className="maaspal-catalog-card__summary">{scenario.summary || scenario.description}</p>
        </Tooltip>
      </CardBody>
      <CardFooter className="maaspal-catalog-card__footer">
        <ScenarioBadges scenario={scenario} />
        <Button
          variant="primary"
          size="sm"
          onClick={(e) => {
            e.stopPropagation();
            onRun(scenario);
          }}
          aria-label={`Run ${title}`}
        >
          Run
        </Button>
      </CardFooter>
    </Card>
  );
}

// Filters live in the URL so coming Back from a run restores them.
function useCatalogFilters() {
  const [params, setParams] = useSearchParams();
  const list = <T extends string>(key: string) =>
    (params.get(key) ?? '').split(',').filter(Boolean) as T[];

  const update = (key: string, value: string | string[]) => {
    const next = new URLSearchParams(params);
    const v = Array.isArray(value) ? value.join(',') : value;
    if (v) next.set(key, v);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return {
    query: params.get('q') ?? '',
    category: params.get('category') ?? '',
    sources: list<Source>('source'),
    types: list<ScenarioType>('type'),
    setQuery: (q: string) => update('q', q),
    setCategory: (c: string) => update('category', c),
    setSources: (s: Source[]) => update('source', s),
    setTypes: (t: ScenarioType[]) => update('type', t),
    clear: () => setParams(new URLSearchParams(), { replace: true }),
    active: params.has('q') || params.has('category') || params.has('source') || params.has('type'),
  };
}

function matchesQuery(s: Scenario, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [scenarioTitle(s), s.summary, s.description, s.name, knownCategory(s)]
    .some((field) => (field ?? '').toLowerCase().includes(q));
}

function toggle<T>(list: T[], value: T, on: boolean): T[] {
  return on ? [...list, value] : list.filter((v) => v !== value);
}

interface Props {
  onRun: (scenario: Scenario) => void;
}

export function ScenarioCatalog({ onRun }: Props) {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const filters = useCatalogFilters();

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listScenarios()
      .then(setScenarios)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load scenarios'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  // Everything but the category filter — what the rail's counts are based on.
  const filtered = useMemo(
    () =>
      scenarios.filter(
        (s) =>
          matchesQuery(s, filters.query) &&
          (filters.sources.length === 0 || filters.sources.includes(scenarioSource(s))) &&
          (filters.types.length === 0 || filters.types.includes(scenarioType(s))),
      ),
    [scenarios, filters.query, filters.sources, filters.types],
  );

  if (loading) return <Spinner aria-label="Loading scenarios" />;

  if (error) {
    return (
      <Alert
        variant="danger"
        title="Could not load scenarios"
        actionLinks={<Button variant="link" onClick={load}>Retry</Button>}
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

  const categories = groupByCategory(scenarios).map(([category]) => category);
  const countIn = (category: string) => filtered.filter((s) => knownCategory(s) === category).length;
  const shown = filters.category ? filtered.filter((s) => knownCategory(s) === filters.category) : filtered;
  const groups = groupByCategory(shown);
  const hasCustom = scenarios.some((s) => s.custom);

  return (
    <div className="maaspal-catalog">
      <aside className="maaspal-catalog__rail" aria-label="Scenario filters">
        <SearchInput
          placeholder="Search scenarios"
          value={filters.query}
          onChange={(_e, v) => filters.setQuery(v)}
          onClear={() => filters.setQuery('')}
          aria-label="Search scenarios"
        />

        <p className="maaspal-section-heading maaspal-catalog__rail-heading">Categories</p>
        <ul className="maaspal-catalog__categories">
          {['', ...categories].map((category) => (
            <li key={category || 'all'}>
              <button
                type="button"
                className="maaspal-catalog__category"
                aria-current={filters.category === category ? 'true' : undefined}
                onClick={() => filters.setCategory(category)}
              >
                <span>{category || 'All'}</span>
                <span className="maaspal-catalog__count">{category ? countIn(category) : filtered.length}</span>
              </button>
            </li>
          ))}
        </ul>

        {hasCustom && (
          <>
            <p className="maaspal-section-heading maaspal-catalog__rail-heading">Source</p>
            {(Object.keys(SOURCE_LABEL) as Source[]).map((source) => (
              <Checkbox
                key={source}
                id={`maaspal-source-${source}`}
                label={SOURCE_LABEL[source]}
                isChecked={filters.sources.includes(source)}
                onChange={(_e, on) => filters.setSources(toggle(filters.sources, source, on))}
              />
            ))}
          </>
        )}

        <p className="maaspal-section-heading maaspal-catalog__rail-heading">Type</p>
        {(Object.keys(TYPE_LABEL) as ScenarioType[]).map((type) => (
          <Checkbox
            key={type}
            id={`maaspal-type-${type}`}
            label={TYPE_LABEL[type]}
            isChecked={filters.types.includes(type)}
            onChange={(_e, on) => filters.setTypes(toggle(filters.types, type, on))}
          />
        ))}

        {filters.active && (
          <Button variant="link" isInline onClick={filters.clear} className="maaspal-catalog__clear">
            Clear filters
          </Button>
        )}
      </aside>

      <div className="maaspal-catalog__results">
        {groups.length === 0 ? (
          <EmptyState titleText="No scenarios match" headingLevel="h3">
            <EmptyStateBody>Try a different search or fewer filters.</EmptyStateBody>
            <EmptyStateFooter>
              <EmptyStateActions>
                <Button variant="link" onClick={filters.clear}>Clear filters</Button>
              </EmptyStateActions>
            </EmptyStateFooter>
          </EmptyState>
        ) : (
          groups.map(([category, items]) => (
            <section key={category} className="maaspal-catalog__group" aria-label={category}>
              <h2 className="maaspal-section-heading maaspal-catalog__group-heading">
                {category} <span className="maaspal-catalog__count">{items.length}</span>
              </h2>
              {CATEGORY_BLURB[category] && <p className="maaspal-catalog__blurb">{CATEGORY_BLURB[category]}</p>}
              <Gallery hasGutter minWidths={{ default: '260px' }}>
                {items.map((s) => (
                  <ScenarioCard key={s.name} scenario={s} onRun={onRun} />
                ))}
              </Gallery>
            </section>
          ))
        )}
        <p className="maaspal-catalog__note">
          Add your own: put a scenario YAML in the BFF&apos;s <code>scenarios/</code> directory. It&apos;s labelled
          Custom and listed first, under its <code>category:</code> if that&apos;s one of the categories here, or
          under Custom otherwise.
        </p>
      </div>
    </div>
  );
}
