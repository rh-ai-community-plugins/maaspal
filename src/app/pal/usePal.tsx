import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { getProgress, listRuns, type Run } from '../api/client';
import {
  applyRun,
  baseline,
  decay,
  derive,
  isTerminal,
  loadActive,
  loadPal,
  newPal,
  saveActive,
  savePal,
  type PalCondition,
  type PalState,
} from './palState';

export const POLL_MS = 10_000;
// Progress files fetched per poll, so a long backlog never floods the BFF.
const MAX_PROGRESS_PER_POLL = 10;

interface PalContextValue {
  active: boolean;
  pal: PalState | null;
  condition: PalCondition | null;
  now: number;
  /** Clicking the logo five times: hatch PAL (in the logo's colourway), or put it away. */
  toggle: (colourway: string) => void;
  /** The hatching animation finished. */
  hatched: () => void;
}

const PalContext = createContext<PalContextValue>({
  active: false,
  pal: null,
  condition: null,
  now: 0,
  toggle: () => undefined,
  hatched: () => undefined,
});

export const usePal = () => useContext(PalContext);

export function PalProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState(loadActive);
  const [pal, setPal] = useState<PalState | null>(loadPal);
  const [runs, setRuns] = useState<Run[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const palRef = useRef(pal);
  palRef.current = pal;

  const update = useCallback((next: PalState | null) => {
    palRef.current = next;
    setPal(next);
    savePal(next);
  }, []);

  const toggle = useCallback(
    (colourway: string) => {
      const on = !active;
      setActive(on);
      saveActive(on);
      // A PAL put away keeps its state; one that never hatched starts fresh.
      if (on && !palRef.current) update(newPal(colourway, Date.now()));
    },
    [active, update],
  );

  const hatched = useCallback(() => {
    const p = palRef.current;
    if (!p || p.hatchedAt !== null) return;
    const t = Date.now();
    // A revived PAL keeps the reaction to the meal that revived it.
    const lastEvent = p.lastEvent?.kind === 'revived' ? p.lastEvent : { kind: 'hatched' as const, at: t };
    update({ ...p, hatchedAt: t, updatedAt: t, lastEvent: { ...lastEvent, at: t } });
  }, [update]);

  // Watch the runs list; every run that finishes since the last poll is a meal.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const poll = async () => {
      let list: Run[];
      try {
        list = (await listRuns()) ?? [];
      } catch {
        return;
      }
      if (cancelled || !palRef.current) return;
      const t = Date.now();
      let next = baseline(decay(palRef.current, t), list);
      const fresh = list
        .filter((r) => isTerminal(r) && !next.seenRuns.includes(r.id))
        .slice(0, MAX_PROGRESS_PER_POLL)
        .reverse(); // oldest first, so the newest run's reaction is the one shown
      for (const run of fresh) {
        let progress = null;
        try {
          progress = (await getProgress(run.id)) ?? null;
        } catch {
          // Counted without its tokens.
        }
        if (cancelled) return;
        next = applyRun(next, run, progress, Date.now());
      }
      setRuns(list);
      setNow(Date.now());
      update(next);
    };
    void poll();
    const id = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [active, update]);

  // A slower clock for bubbles and cooldowns between polls.
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 2_000);
    return () => window.clearInterval(id);
  }, [active]);

  const condition = useMemo(() => (pal ? derive(pal, runs, now) : null), [pal, runs, now]);
  const value = useMemo(
    () => ({ active, pal: active ? pal : null, condition: active ? condition : null, now, toggle, hatched }),
    [active, pal, condition, now, toggle, hatched],
  );
  return <PalContext.Provider value={value}>{children}</PalContext.Provider>;
}

/** Counts quick clicks on the header logo: `onClick` returns true on the
 * fifth click within the window. */
export function useSecretClicks(needed = 5, windowMs = 2_000) {
  const clicks = useRef<number[]>([]);
  return useCallback(() => {
    const t = Date.now();
    clicks.current = [...clicks.current.filter((c) => t - c < windowMs), t];
    if (clicks.current.length >= needed) {
      clicks.current = [];
      return true;
    }
    return false;
  }, [needed, windowMs]);
}
