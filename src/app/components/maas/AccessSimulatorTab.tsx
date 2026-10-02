import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Label,
  LabelGroup,
  MenuToggle,
  Select,
  SelectGroup,
  SelectList,
  SelectOption,
  Spinner,
  TextInputGroup,
  TextInputGroupMain,
  TextInputGroupUtilities,
} from '@patternfly/react-core';
// CJS path, not the usual dist/esm/... deep import — Jest's config here has
// no transform for ESM node_modules, and this is the only place react-icons
// is used so far. Works for both Jest (native CJS) and webpack.
import TimesIcon from '@patternfly/react-icons/dist/js/icons/times-icon';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import {
  getMaasAuthPolicies,
  getMaasModels,
  getMaasSubscriptions,
  type MaasAuthPolicy,
  type MaasModel,
  type MaasSubscription,
} from '../../api/client';
import { MaasUnavailableNotice } from './MaasUnavailableNotice';

const POLL_INTERVAL_MS = 25000;

interface ResolvedRow {
  model: MaasModel;
  winningSubscription: MaasSubscription | null;
  matchingSubscriptions: MaasSubscription[];
  matchingPolicies: MaasAuthPolicy[];
  reachable: boolean;
}

function modelKey(namespace: string, name: string): string {
  return `${namespace}/${name}`;
}

function ownerMatches(owner: { groups: string[]; users: string[] }, candidates: string[]): boolean {
  return owner.groups.some((g) => candidates.includes(g)) || owner.users.some((u) => candidates.includes(u));
}

// The whole point: resolve access the way MaaS itself would for a given set
// of candidate groups/users — highest-priority matching subscription wins
// the quota question, and a model is only actually reachable when a
// matching auth policy ALSO exists for the same candidate set (see Catalog
// item I / C). Candidates can be group names or usernames — a subscription/
// auth policy can name either directly via owner.users, not just owner.groups.
// Purely client-side over data already fetched — no new backend endpoint.
function resolveAccess(
  models: MaasModel[],
  subscriptions: MaasSubscription[],
  authPolicies: MaasAuthPolicy[],
  candidates: string[],
): ResolvedRow[] {
  if (candidates.length === 0) return [];

  return models.map((model) => {
    const key = modelKey(model.namespace, model.name);

    const matchingSubscriptions = subscriptions.filter(
      (s) => ownerMatches(s.owner, candidates) && s.model_refs.some((m) => modelKey(m.namespace, m.name) === key),
    );
    const matchingPolicies = authPolicies.filter(
      (p) => ownerMatches(p.owner, candidates) && p.model_refs.some((m) => modelKey(m.namespace, m.name) === key),
    );

    const winningSubscription =
      matchingSubscriptions.length === 0
        ? null
        : matchingSubscriptions.reduce((best, s) =>
            (s.priority ?? -Infinity) > (best.priority ?? -Infinity) ? s : best,
          );

    return {
      model,
      winningSubscription,
      matchingSubscriptions,
      matchingPolicies,
      reachable: matchingSubscriptions.length > 0 && matchingPolicies.length > 0,
    };
  });
}

// Suggestions come from groups/users already referenced by a subscription or
// auth policy's owner/subjects — the only ones that could ever actually
// affect the resolution, so nothing to filter (unreferenced OpenShift Groups
// wouldn't change any result and would just be typeahead noise).
function useKnownIdentifiers(subscriptions: MaasSubscription[], authPolicies: MaasAuthPolicy[]) {
  return useMemo(() => {
    const groups = new Set<string>();
    const users = new Set<string>();
    for (const s of subscriptions) {
      s.owner.groups.forEach((g) => groups.add(g));
      s.owner.users.forEach((u) => users.add(u));
    }
    for (const p of authPolicies) {
      p.owner.groups.forEach((g) => groups.add(g));
      p.owner.users.forEach((u) => users.add(u));
    }
    return { groups: Array.from(groups).sort(), users: Array.from(users).sort() };
  }, [subscriptions, authPolicies]);
}

function CandidateTypeahead({
  knownGroups,
  knownUsers,
  selected,
  onChange,
}: {
  knownGroups: string[];
  knownUsers: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [filterValue, setFilterValue] = useState('');
  const textInputRef = useRef<HTMLInputElement>(null);

  const filteredGroups = knownGroups.filter(
    (g) => !selected.includes(g) && g.toLowerCase().includes(filterValue.toLowerCase()),
  );
  const filteredUsers = knownUsers.filter(
    (u) => !selected.includes(u) && u.toLowerCase().includes(filterValue.toLowerCase()),
  );
  const hasResults = filteredGroups.length > 0 || filteredUsers.length > 0;

  function addCandidate(value: string) {
    const trimmed = value.trim();
    if (trimmed && !selected.includes(trimmed)) {
      onChange([...selected, trimmed]);
    }
  }

  function onInputChange(_event: React.FormEvent<HTMLInputElement>, value: string) {
    // Paste-friendly: "team-a, team-b" adds each finished segment as a chip
    // and keeps typing the last (possibly incomplete) one.
    if (value.includes(',')) {
      const parts = value.split(',');
      const toAdd = parts.slice(0, -1).map((p) => p.trim()).filter(Boolean);
      const remainder = parts[parts.length - 1];
      const merged = [...selected, ...toAdd.filter((c) => !selected.includes(c))];
      onChange(merged);
      setInputValue(remainder);
      setFilterValue(remainder);
    } else {
      setInputValue(value);
      setFilterValue(value);
    }
    if (!isOpen) setIsOpen(true);
  }

  function onSelectOption(_event: React.MouseEvent | undefined, value: string | number | undefined) {
    if (typeof value === 'string') addCandidate(value);
    setInputValue('');
    setFilterValue('');
    textInputRef.current?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault();
      addCandidate(inputValue);
      setInputValue('');
      setFilterValue('');
    } else if (event.key === 'Backspace' && inputValue === '' && selected.length > 0) {
      onChange(selected.slice(0, -1));
    } else if (event.key === 'Escape') {
      setIsOpen(false);
    }
  }

  function removeCandidate(value: string) {
    onChange(selected.filter((c) => c !== value));
    textInputRef.current?.focus();
  }

  return (
    <Select
      isOpen={isOpen}
      onOpenChange={setIsOpen}
      onSelect={onSelectOption}
      toggle={(toggleRef) => (
        <MenuToggle
          ref={toggleRef}
          variant="typeahead"
          isExpanded={isOpen}
          isFullWidth
          style={{ maxWidth: '500px' }}
          onClick={() => setIsOpen((v) => !v)}
        >
          <TextInputGroup isPlain>
            <TextInputGroupMain
              value={inputValue}
              onClick={() => setIsOpen(true)}
              onChange={onInputChange}
              onKeyDown={onKeyDown}
              innerRef={textInputRef}
              autoComplete="off"
              placeholder={selected.length === 0 ? 'e.g. system:authenticated, premium-users' : ''}
              aria-label="Candidate groups or users"
            >
              <LabelGroup>
                {selected.map((c) => (
                  <Label
                    key={c}
                    variant="outline"
                    onClose={(evt) => {
                      evt.stopPropagation();
                      removeCandidate(c);
                    }}
                  >
                    {c}
                  </Label>
                ))}
              </LabelGroup>
            </TextInputGroupMain>
            {(inputValue || selected.length > 0) && (
              <TextInputGroupUtilities>
                <Button
                  variant="plain"
                  aria-label="Clear all"
                  onClick={() => {
                    onChange([]);
                    setInputValue('');
                    setFilterValue('');
                    textInputRef.current?.focus();
                  }}
                  icon={<TimesIcon />}
                />
              </TextInputGroupUtilities>
            )}
          </TextInputGroup>
        </MenuToggle>
      )}
    >
      <SelectList>
        {!hasResults ? (
          <SelectOption isDisabled>
            {filterValue ? 'No matching groups or users — press Enter to use it anyway' : 'No known groups or users yet'}
          </SelectOption>
        ) : (
          <>
            {filteredGroups.length > 0 && (
              <SelectGroup label="Groups">
                {filteredGroups.map((g) => (
                  <SelectOption key={g} value={g}>
                    {g}
                  </SelectOption>
                ))}
              </SelectGroup>
            )}
            {filteredUsers.length > 0 && (
              <SelectGroup label="Users">
                {filteredUsers.map((u) => (
                  <SelectOption key={u} value={u}>
                    {u}
                  </SelectOption>
                ))}
              </SelectGroup>
            )}
          </>
        )}
      </SelectList>
    </Select>
  );
}

export function AccessSimulatorTab() {
  const [models, setModels] = useState<MaasModel[] | null>(null);
  const [modelsUnavailable, setModelsUnavailable] = useState<string | null | undefined>(undefined);
  const [subscriptions, setSubscriptions] = useState<MaasSubscription[] | null>(null);
  const [subscriptionsUnavailable, setSubscriptionsUnavailable] = useState<string | null | undefined>(undefined);
  const [authPolicies, setAuthPolicies] = useState<MaasAuthPolicy[] | null>(null);
  const [authPoliciesUnavailable, setAuthPoliciesUnavailable] = useState<string | null | undefined>(undefined);
  const [candidates, setCandidates] = useState<string[]>([]);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    const [modelsResult, subsResult, authResult] = await Promise.all([
      getMaasModels(),
      getMaasSubscriptions(),
      getMaasAuthPolicies(),
    ]);
    if (modelsResult.available) {
      setModels(modelsResult.items);
      setModelsUnavailable(null);
    } else {
      setModels(null);
      setModelsUnavailable(modelsResult.reason ?? 'unknown');
    }
    if (subsResult.available) {
      setSubscriptions(subsResult.items);
      setSubscriptionsUnavailable(null);
    } else {
      setSubscriptions(null);
      setSubscriptionsUnavailable(subsResult.reason ?? 'unknown');
    }
    if (authResult.available) {
      setAuthPolicies(authResult.items);
      setAuthPoliciesUnavailable(null);
    } else {
      setAuthPolicies(null);
      setAuthPoliciesUnavailable(authResult.reason ?? 'unknown');
    }
  }

  useEffect(() => {
    void fetchData();
    intervalRef.current = setInterval(() => { void fetchData(); }, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current);
    };
  }, []);

  const { groups: knownGroups, users: knownUsers } = useKnownIdentifiers(subscriptions ?? [], authPolicies ?? []);

  const resolved = useMemo(
    () => resolveAccess(models ?? [], subscriptions ?? [], authPolicies ?? [], candidates),
    [models, subscriptions, authPolicies, candidates],
  );

  if (modelsUnavailable === undefined) return <Spinner size="md" aria-label="Loading access simulator" />;
  if (modelsUnavailable) return <MaasUnavailableNotice reason={modelsUnavailable} />;
  if (subscriptionsUnavailable) return <MaasUnavailableNotice reason={subscriptionsUnavailable} />;
  if (authPoliciesUnavailable) return <MaasUnavailableNotice reason={authPoliciesUnavailable} />;

  return (
    <>
      <p style={{ color: '#555', marginBottom: '0.75rem' }}>
        Enter a candidate set of groups and/or users to see which subscription would win by
        priority for each model, and whether that set can actually reach it — the same resolution
        MaaS itself performs, computed here from live subscriptions and auth policies. Start typing
        to see known groups/users, or enter a name that doesn&apos;t exist yet to test a hypothetical one.
      </p>
      <div style={{ marginBottom: '1rem' }}>
        <CandidateTypeahead
          knownGroups={knownGroups}
          knownUsers={knownUsers}
          selected={candidates}
          onChange={setCandidates}
        />
      </div>

      {candidates.length === 0 ? (
        <p style={{ color: '#888', fontStyle: 'italic' }}>
          Enter one or more group/user names above to simulate access resolution.
        </p>
      ) : (
        <Table aria-label="Access resolution simulation">
          <Thead>
            <Tr>
              <Th>Model</Th>
              <Th>Resolved subscription</Th>
              <Th modifier="wrap">Matching auth policies</Th>
              <Th>Reachable</Th>
            </Tr>
          </Thead>
          <Tbody>
            {resolved.map((row) => (
              <Tr key={modelKey(row.model.namespace, row.model.name)}>
                <Td>
                  <div style={{ fontWeight: 700 }}>{row.model.display_name}</div>
                  <div style={{ fontSize: '0.75rem', fontFamily: 'monospace', color: '#888' }}>
                    {row.model.namespace}
                  </div>
                </Td>
                <Td>
                  {row.winningSubscription ? (
                    <>
                      <Label isCompact color="blue">
                        {row.winningSubscription.display_name} (p{row.winningSubscription.priority ?? '—'})
                      </Label>
                      {row.matchingSubscriptions.length > 1 && (
                        <div style={{ fontSize: '0.72rem', color: '#888', marginTop: '0.2rem' }}>
                          beat {row.matchingSubscriptions.length - 1} other matching subscription
                          {row.matchingSubscriptions.length > 2 ? 's' : ''}
                        </div>
                      )}
                    </>
                  ) : (
                    <span style={{ color: '#888' }}>no quota for this group set</span>
                  )}
                </Td>
                <Td>
                  {row.matchingPolicies.length === 0 ? (
                    <span style={{ color: '#888' }}>none</span>
                  ) : (
                    row.matchingPolicies.map((p) => (
                      <Label key={p.name} isCompact color="blue" style={{ marginRight: '0.3rem', marginBottom: '0.2rem' }}>
                        {p.display_name}
                      </Label>
                    ))
                  )}
                </Td>
                <Td>
                  <Label isCompact color={row.reachable ? 'green' : 'red'}>
                    {row.reachable ? '✓ reachable' : '✗ not reachable'}
                  </Label>
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      )}
    </>
  );
}
