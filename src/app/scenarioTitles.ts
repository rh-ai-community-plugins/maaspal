import { useEffect, useState } from 'react';
import { listScenarios, type Scenario } from './api/client';

export function formatScenarioName(name: string): string {
  return name.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

export function formatTaskName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function scenarioTitle(s: Scenario): string {
  return s.title || formatScenarioName(s.name);
}

/** scenario id → display title, including every id a scenario used to have
 * (`previous_names`), so run-history rows from before a rename still show
 * the scenario's current title. */
export function buildTitleMap(scenarios: Scenario[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const s of scenarios) {
    const title = scenarioTitle(s);
    map.set(s.name, title);
    for (const old of s.previous_names ?? []) {
      if (!map.has(old)) map.set(old, title);
    }
  }
  return map;
}

// One fetch shared by every caller in the page's lifetime — the scenario list
// only changes on redeploy.
let cached: Promise<Map<string, string>> | null = null;

function loadTitleMap(): Promise<Map<string, string>> {
  if (!cached) {
    // Promise chain (not a direct call) so any failure — including a mocked
    // client returning nothing — degrades to formatted ids, never a crash.
    cached = Promise.resolve()
      .then(() => listScenarios())
      .then((scenarios) => buildTitleMap(scenarios ?? []))
      .catch(() => {
        cached = null;
        return new Map<string, string>();
      });
  }
  return cached;
}

/** Resolves a scenario id to its display title — the formatted id until the
 * scenario list has loaded (or if the scenario no longer exists). */
export function useScenarioTitle(): (name: string) => string {
  const [titles, setTitles] = useState<Map<string, string>>(new Map());
  useEffect(() => {
    let alive = true;
    void loadTitleMap().then((m) => {
      if (alive) setTitles(m);
    });
    return () => {
      alive = false;
    };
  }, []);
  return (name: string) => titles.get(name) ?? formatScenarioName(name);
}

/** Test hook: forget the cached scenario list. */
export function resetScenarioTitleCache(): void {
  cached = null;
}
