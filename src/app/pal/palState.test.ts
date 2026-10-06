import type { ProgressResponse, Run } from '../api/client';
import {
  applyRun,
  baseline,
  bubbleText,
  decay,
  derive,
  EXPIRY_MS,
  loadPal,
  newPal,
  savePal,
  stageFor,
  THROTTLE_COOLDOWN_MS,
  type PalState,
} from './palState';

const T0 = Date.UTC(2026, 9, 6, 12);
const HOUR = 3_600_000;

function run(id: string, over: Partial<Run> = {}): Run {
  return {
    id,
    scenario: 'smoke_test',
    status: 'PASS',
    created_at: '',
    updated_at: '',
    auto_cleanup: true,
    cleanup_status: 'done',
    ...over,
  };
}

function traffic(tokens: number, rateLimited = 0): ProgressResponse {
  return {
    tasks: [],
    traffic: [
      { task: 'send_requests', result_key: 'inference_results', summary: { total_tokens_sent: tokens, rate_limited_count: rateLimited }, timeline: [] },
    ],
  };
}

function hatched(over: Partial<PalState> = {}): PalState {
  return { ...newPal('pink', T0), hatchedAt: T0, baselined: true, ...over };
}

test('an egg does not decay, a hatched PAL gets hungrier over time', () => {
  expect(decay(newPal('pink', T0), T0 + 10 * HOUR).fullness).toBe(60);
  const later = decay(hatched({ fullness: 60 }), T0 + 10 * HOUR);
  expect(later.fullness).toBe(20);
  expect(derive(later, [], T0 + 10 * HOUR).hungry).toBe(true);
});

test('runs that finished before hatching are not a meal', () => {
  const pal = baseline({ ...hatched(), baselined: false }, [run('old'), run('live', { status: 'RUNNING' })]);
  expect(pal.seenRuns).toEqual(['old']);
  expect(applyRun(pal, run('old'), traffic(500), T0)).toBe(pal);
});

test('a finished run feeds PAL its tokens, once', () => {
  const pal = applyRun(hatched(), run('r1'), traffic(1_000), T0);
  expect(pal.lifetimeTokens).toBe(1_000);
  expect(pal.fullness).toBeGreaterThan(60);
  expect(pal.mood).toBe(75);
  expect(pal.lastEvent).toMatchObject({ kind: 'ate', tokens: 1_000 });
  expect(applyRun(pal, run('r1'), traffic(1_000), T0)).toBe(pal);
});

test('feed_pal counts double and failures sour the mood', () => {
  expect(applyRun(hatched(), run('f', { scenario: 'feed_pal' }), traffic(100), T0).lifetimeTokens).toBe(200);
  expect(applyRun(hatched(), run('x', { status: 'FAIL' }), null, T0).mood).toBe(40);
});

test('a throttled run leaves PAL panting for the cooldown', () => {
  const pal = applyRun(hatched(), run('r'), traffic(30, 2), T0);
  expect(derive(pal, [], T0).throttled).toBe(true);
  expect(bubbleText(pal, derive(pal, [], T0), false, T0)).toBe('429 Too Many Treats');
  expect(derive(pal, [], T0 + THROTTLE_COOLDOWN_MS + 1).throttled).toBe(false);
});

test('leftover resources litter the floor until cleaned up', () => {
  const runs = [
    run('a', { cleanup_status: 'failed' }),
    run('b', { auto_cleanup: false, cleanup_status: 'skipped' }),
    run('c', { auto_cleanup: false, cleanup_status: 'done' }),
    run('d', { status: 'RUNNING', auto_cleanup: false, cleanup_status: 'pending' }),
  ];
  const c = derive(hatched(), runs, T0);
  expect(c.litter).toBe(2);
  expect(c.busy).toBe(true);
});

test('PAL is sick while the latest health check failed', () => {
  const failing = [run('2', { scenario: 'smoke_test', status: 'FAIL' }), run('1', { scenario: 'smoke_test' })];
  expect(derive(hatched(), failing, T0).sick).toBe(true);
  expect(derive(hatched(), [run('3', { scenario: 'smoke_test' }), ...failing], T0).sick).toBe(false);
});

test('PAL grows with lifetime tokens', () => {
  expect(stageFor(newPal('pink', T0))).toBe('egg');
  expect(stageFor(hatched())).toBe('baby');
  expect(stageFor(hatched({ lifetimeTokens: 1_000 }))).toBe('child');
  expect(stageFor(hatched({ lifetimeTokens: 50_000 }))).toBe('teen');
  expect(stageFor(hatched({ lifetimeTokens: 2_000_000 }))).toBe('guardian');
});

test('a week unfed expires PAL; only feed_pal brings it back, as an egg', () => {
  const ghost = decay(hatched(), T0 + EXPIRY_MS + 1);
  expect(ghost.expired).toBe(true);
  expect(applyRun(ghost, run('s'), traffic(500), T0).expired).toBe(true);
  const revived = applyRun(ghost, run('f', { scenario: 'feed_pal' }), traffic(500), T0);
  expect(revived.expired).toBe(false);
  expect(revived.hatchedAt).toBeNull();
  expect(revived.lastEvent?.kind).toBe('revived');
});

test('storage that throws never breaks PAL', () => {
  const get = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  const set = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  expect(loadPal()).toBeNull();
  expect(() => savePal(hatched())).not.toThrow();
  get.mockRestore();
  set.mockRestore();
});
