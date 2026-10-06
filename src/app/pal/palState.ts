// PAL the pet (the easter egg): the pure logic, no React. Every need is driven
// by real harness use — runs feed it tokens and set its mood, leftover
// resources litter its floor — so nothing here has buttons to press.
import type { ProgressResponse, Run } from '../api/client';

export type PalStage = 'egg' | 'baby' | 'child' | 'teen' | 'guardian';
export type PalEventKind = 'hatched' | 'ate' | 'throttled' | 'pass' | 'fail' | 'cancelled' | 'revived' | 'ghost';

export interface PalEvent {
  kind: PalEventKind;
  at: number; // ms since the epoch
  tokens?: number;
}

export interface PalState {
  version: 1;
  // Set when the hatching animation finishes; null = still an egg.
  hatchedAt: number | null;
  colourway: string;
  lifetimeTokens: number;
  fullness: number; // 0–100
  mood: number; // 0–100
  lastFedAt: number | null;
  lastMealTokens: number;
  // When decay was last applied.
  updatedAt: number;
  // Terminal runs already counted. Runs that had finished before PAL hatched
  // are marked seen without feeding it (baselined).
  seenRuns: string[];
  baselined: boolean;
  throttledUntil: number | null;
  expired: boolean;
  lastEvent: PalEvent | null;
}

export const FEED_PAL = 'feed_pal';
const HEALTH_SCENARIOS = new Set(['smoke_test', 'model_config_health']);
const ACTIVE = new Set(['PENDING', 'RUNNING']);
const HOUR = 3_600_000;
export const EXPIRY_MS = 7 * 24 * HOUR;
export const THROTTLE_COOLDOWN_MS = 60_000;
const FULLNESS_DECAY_PER_HOUR = 4; // full → empty in about a day
const MOOD_DRIFT_PER_HOUR = 2; // back towards neutral
const MAX_SEEN = 300;

// Lifetime tokens at which PAL grows into each stage.
export const STAGES: [PalStage, number][] = [
  ['baby', 0],
  ['child', 1_000],
  ['teen', 50_000],
  ['guardian', 1_000_000],
];

export const STAGE_TITLE: Record<PalStage, string> = {
  egg: 'Egg',
  baby: 'Baby PAL',
  child: 'Little PAL',
  teen: 'Teen PAL',
  guardian: 'Gateway Guardian',
};

export function newPal(colourway: string, now: number): PalState {
  return {
    version: 1,
    hatchedAt: null,
    colourway,
    lifetimeTokens: 0,
    fullness: 60,
    mood: 60,
    lastFedAt: null,
    lastMealTokens: 0,
    updatedAt: now,
    seenRuns: [],
    baselined: false,
    throttledUntil: null,
    expired: false,
    lastEvent: null,
  };
}

const clamp = (n: number) => Math.max(0, Math.min(100, n));

export function stageFor(state: PalState): PalStage {
  if (state.hatchedAt === null) return 'egg';
  let stage: PalStage = 'baby';
  for (const [s, min] of STAGES) if (state.lifetimeTokens >= min) stage = s;
  return stage;
}

/** Fullness and mood change with real time passing; a PAL left unfed for a
 * week has its key expire. */
export function decay(state: PalState, now: number): PalState {
  if (state.hatchedAt === null) return { ...state, updatedAt: now };
  const hours = Math.max(0, now - state.updatedAt) / HOUR;
  const drift = MOOD_DRIFT_PER_HOUR * hours;
  const mood = state.mood > 50 ? Math.max(50, state.mood - drift) : Math.min(50, state.mood + drift);
  const lastMeal = state.lastFedAt ?? state.hatchedAt;
  const expired = state.expired || now - lastMeal > EXPIRY_MS;
  return {
    ...state,
    fullness: expired ? 0 : clamp(state.fullness - FULLNESS_DECAY_PER_HOUR * hours),
    mood: expired ? 0 : mood,
    expired,
    updatedAt: now,
    lastEvent: expired && !state.expired ? { kind: 'ghost', at: now } : state.lastEvent,
  };
}

/** Tokens a run's requests used, from its traffic summaries. */
export function tokensEaten(progress: ProgressResponse | null | undefined): number {
  return (progress?.traffic ?? []).reduce((sum, b) => sum + (b.summary?.total_tokens_sent ?? 0), 0);
}

export function wasThrottled(progress: ProgressResponse | null | undefined): boolean {
  return (progress?.traffic ?? []).some((b) => (b.summary?.rate_limited_count ?? 0) > 0);
}

// A smoke test's few hundred tokens and a load test's millions should both
// feel like a meal, so fullness grows with the order of magnitude.
export function fullnessGain(tokens: number): number {
  return tokens > 0 ? 10 * Math.log10(1 + tokens) : 0;
}

export const isTerminal = (run: Run) => !ACTIVE.has(run.status);

/** Mark every run that had already finished as seen, so PAL is only fed by
 * what happens after it hatches. */
export function baseline(state: PalState, runs: Run[]): PalState {
  if (state.baselined) return state;
  return { ...state, baselined: true, seenRuns: runs.filter(isTerminal).map((r) => r.id).slice(0, MAX_SEEN) };
}

/** Count one finished run: it feeds PAL its tokens (double for feed_pal),
 * nudges its mood by the result, and a throttled run leaves it panting. A
 * feed_pal run brings an expired PAL back (it hatches again). */
export function applyRun(state: PalState, run: Run, progress: ProgressResponse | null, now: number): PalState {
  if (state.seenRuns.includes(run.id)) return state;
  const seenRuns = [run.id, ...state.seenRuns].slice(0, MAX_SEEN);
  const isFeed = run.scenario === FEED_PAL;
  const tokens = tokensEaten(progress);

  if (state.expired) {
    if (!isFeed || tokens === 0) return { ...state, seenRuns };
    return {
      ...newPal(state.colourway, now),
      baselined: true,
      seenRuns,
      lifetimeTokens: tokens * 2,
      fullness: clamp(30 + fullnessGain(tokens) * 2),
      lastFedAt: now,
      lastMealTokens: tokens,
      lastEvent: { kind: 'revived', at: now, tokens },
    };
  }

  const moodDelta =
    run.status === 'PASS' ? (isFeed ? 25 : 15) : run.status === 'FAIL' ? -20 : run.status === 'CANCELLED' ? -5 : 0;
  const credit = isFeed ? tokens * 2 : tokens;
  const throttled = wasThrottled(progress);
  const kind: PalEventKind = throttled
    ? 'throttled'
    : tokens > 0
      ? 'ate'
      : run.status === 'PASS'
        ? 'pass'
        : run.status === 'FAIL'
          ? 'fail'
          : 'cancelled';
  return {
    ...state,
    seenRuns,
    lifetimeTokens: state.lifetimeTokens + credit,
    fullness: clamp(state.fullness + fullnessGain(tokens) * (isFeed ? 2 : 1)),
    mood: clamp(state.mood + moodDelta),
    lastFedAt: tokens > 0 ? now : state.lastFedAt,
    lastMealTokens: tokens > 0 ? tokens : state.lastMealTokens,
    throttledUntil: throttled ? now + THROTTLE_COOLDOWN_MS : state.throttledUntil,
    lastEvent: { kind, at: now, tokens },
  };
}

/** Runs whose resources were left behind: cleanup failed, or auto cleanup was
 * off and nobody has pressed Clean Up Now yet. */
export function leftoverRuns(runs: Run[]): Run[] {
  return runs.filter(
    (r) => isTerminal(r) && (r.cleanup_status === 'failed' || (!r.auto_cleanup && r.cleanup_status !== 'done')),
  );
}

export interface PalCondition {
  stage: PalStage;
  litter: number;
  sick: boolean;
  busy: boolean;
  throttled: boolean;
  hungry: boolean;
  expired: boolean;
  moodLabel: 'happy' | 'okay' | 'grumpy';
}

/** What PAL looks like right now, from its state plus the live runs list. */
export function derive(state: PalState, runs: Run[], now: number): PalCondition {
  // Newest first, as the API lists them.
  const lastHealth = runs.find((r) => HEALTH_SCENARIOS.has(r.scenario) && isTerminal(r));
  return {
    stage: stageFor(state),
    litter: leftoverRuns(runs).length,
    sick: lastHealth?.status === 'FAIL',
    busy: runs.some((r) => !isTerminal(r)),
    throttled: state.throttledUntil !== null && state.throttledUntil > now,
    hungry: state.fullness < 25,
    expired: state.expired,
    moodLabel: state.mood >= 65 ? 'happy' : state.mood >= 35 ? 'okay' : 'grumpy',
  };
}

const EVENT_SHOWN_MS = 8_000;

/** The speech bubble: a fresh event first, else the most pressing need. */
export function bubbleText(state: PalState, c: PalCondition, sleeping: boolean, now: number): string | null {
  const e = state.lastEvent;
  if (e && now - e.at < EVENT_SHOWN_MS) {
    switch (e.kind) {
      case 'hatched':
        return 'Hello, world!';
      case 'ate':
        return `Nom nom, ${(e.tokens ?? 0).toLocaleString()} tokens!`;
      case 'throttled':
        return '429 Too Many Treats';
      case 'pass':
        return 'All checks passed! ♥';
      case 'fail':
        return 'A check failed… :(';
      case 'cancelled':
        return 'Hmph. Stopped early.';
      case 'revived':
        return 'Re-provisioned! Hello, world!';
      case 'ghost':
        return "PAL's key expired…";
    }
  }
  if (c.expired) return "PAL's key expired. Feed it to re-provision.";
  if (c.throttled) return '429 Too Many Treats';
  if (c.sick) return 'Not feeling well. Is MaaS okay?';
  if (sleeping) return 'Zzz…';
  if (c.busy) return 'Working on a run…';
  if (c.litter > 0) return c.litter === 1 ? 'Someone left a key lying around.' : `Who left ${c.litter} keys here?`;
  if (c.hungry) return 'Feed me tokens…';
  return null;
}

// ---- Storage: a per-viewer convenience; everything works without it. ----

const STATE_KEY = 'maaspal.pal.state';
const ACTIVE_KEY = 'maaspal.pal.active';

export function loadPal(): PalState | null {
  try {
    const raw = window.localStorage.getItem(STATE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PalState;
    return parsed?.version === 1 ? parsed : null;
  } catch {
    return null;
  }
}

export function savePal(state: PalState | null): void {
  try {
    if (state) window.localStorage.setItem(STATE_KEY, JSON.stringify(state));
    else window.localStorage.removeItem(STATE_KEY);
  } catch {
    // Private window or blocked storage: PAL just won't remember.
  }
}

export function loadActive(): boolean {
  try {
    return window.localStorage.getItem(ACTIVE_KEY) === '1';
  } catch {
    return false;
  }
}

export function saveActive(active: boolean): void {
  try {
    if (active) window.localStorage.setItem(ACTIVE_KEY, '1');
    else window.localStorage.removeItem(ACTIVE_KEY);
  } catch {
    // As above.
  }
}
