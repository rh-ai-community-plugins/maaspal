import type { BrowserOrder, BrowserRecord } from './api/client';
import { readStreamEvent, requestBody, runBurst, sendOne, SseParser } from './browserSender';

function order(overrides: Partial<BrowserOrder> = {}): BrowserOrder {
  return {
    order_id: 'o1',
    claim_id: 'c1',
    result_key: 'inference_results',
    step: 'send_requests',
    api: 'chat_completions',
    path: '/chat/completions',
    stream: false,
    prompt: 'Hi',
    batch_size: 1,
    concurrency: 2,
    retries: 0,
    timeout_s: null,
    stop_after_429s: 0,
    stop_after_transport_errors: 0,
    targets: [
      { url: 'https://maas.test/v1', model: 'm1', key: 'sk-oai-1' },
      { url: 'https://maas.test/ns/m2/v1/', model: 'm2', key: 'sk-oai-2' },
    ],
    plan: [0, 1, 0],
    ...overrides,
  };
}

// A minimal stand-in for fetch's Response (jsdom has none).
function reply(
  status: number,
  body: unknown,
  { headers = {}, chunks }: { headers?: Record<string, string>; chunks?: string[] } = {},
): Response {
  const encoder = new TextEncoder();
  const queue = (chunks ?? []).map((c) => encoder.encode(c));
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 429 ? 'Too Many Requests' : '',
    headers: { get: (k: string) => headers[k.toLowerCase()] ?? null },
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
    body: chunks
      ? {
          getReader: () => ({
            read: async () => (queue.length ? { done: false, value: queue.shift() } : { done: true, value: undefined }),
          }),
          cancel: async () => undefined,
        }
      : null,
  } as unknown as Response;
}

const deps = (fetchImpl: jest.Mock) => {
  let t = 0;
  return { fetchImpl: fetchImpl as unknown as typeof fetch, now: () => (t += 10), sleep: async () => undefined };
};

test('request bodies match the harness for each request API', () => {
  expect(requestBody(order(), 'm')).toEqual({ model: 'm', messages: [{ role: 'user', content: 'Hi' }] });
  expect(requestBody(order({ stream: true }), 'm')).toMatchObject({ stream: true, stream_options: { include_usage: true } });
  expect(requestBody(order({ api: 'completions', batch_size: 2 }), 'm')).toEqual({ model: 'm', prompt: ['Hi', 'Hi'] });
  expect(requestBody(order({ api: 'responses' }), 'm')).toEqual({ model: 'm', input: 'Hi' });
  expect(requestBody(order({ api: 'embeddings' }), 'm')).toEqual({ model: 'm', input: 'Hi' });
});

test('SSE events are split across chunk boundaries', () => {
  const p = new SseParser();
  expect(p.push('data: {"a"')).toEqual([]);
  expect(p.push(':1}\r\n\r\ndata: [DONE]\n\n')).toEqual(['{"a":1}', '[DONE]']);
});

test('stream events: text and the final usage', () => {
  expect(readStreamEvent('chat_completions', '{"choices":[{"delta":{"content":"x"}}]}')).toEqual({
    text: true,
    usage: undefined,
  });
  expect(readStreamEvent('chat_completions', '{"choices":[],"usage":{"total_tokens":7}}').usage).toEqual({
    total_tokens: 7,
  });
  expect(readStreamEvent('responses', '{"type":"response.completed","response":{"usage":{"input_tokens":3}}}')).toEqual({
    text: false,
    usage: { input_tokens: 3 },
  });
});

test('a successful reply carries its usage and the key goes only to MaaS', async () => {
  const fetchImpl = jest.fn().mockResolvedValue(reply(200, { usage: { prompt_tokens: 2, total_tokens: 9 } }));
  const rec = await sendOne(order(), order().targets[1], new AbortController().signal, 0, deps(fetchImpl));
  expect(rec).toMatchObject({ ok: true, status: 200, usage: { prompt_tokens: 2, total_tokens: 9 } });
  const [url, init] = fetchImpl.mock.calls[0];
  expect(url).toBe('https://maas.test/ns/m2/v1/chat/completions');
  expect(init.headers.Authorization).toBe('Bearer sk-oai-2');
  expect(init.credentials).toBe('omit');
});

test('a streamed reply records when its text arrived', async () => {
  const fetchImpl = jest.fn().mockResolvedValue(
    reply(200, null, {
      chunks: [
        'data: {"choices":[{"delta":{"content":"a"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"b"}}]}\n\ndata: {"choices":[],"usage":{"total_tokens":5}}\n\n',
      ],
    }),
  );
  const rec = await sendOne(order({ stream: true }), order().targets[0], new AbortController().signal, 0, deps(fetchImpl));
  expect(rec.arrivals).toHaveLength(2);
  expect(rec.usage).toEqual({ total_tokens: 5 });
});

test('retries like the SDK and reports every attempt', async () => {
  const fetchImpl = jest
    .fn()
    .mockResolvedValueOnce(reply(503, 'busy'))
    .mockResolvedValueOnce(reply(429, '', { headers: { 'retry-after': '0' } }))
    .mockResolvedValueOnce(reply(200, { usage: { total_tokens: 1 } }));
  const rec = await sendOne(order({ retries: 2 }), order().targets[0], new AbortController().signal, 0, deps(fetchImpl));
  expect(rec.ok).toBe(true);
  expect(rec.attempts?.map((a) => a.status)).toEqual([503, 429, 200]);
});

test('a failed request keeps status, body and the readable headers', async () => {
  const fetchImpl = jest
    .fn()
    .mockResolvedValue(reply(403, '', { headers: { 'x-ext-auth-reason': 'no policy', 'set-cookie': 'x' } }));
  const rec = await sendOne(order(), order().targets[0], new AbortController().signal, 0, deps(fetchImpl));
  expect(rec).toMatchObject({ ok: false, status: 403, headers: { 'x-ext-auth-reason': 'no policy' } });
  expect(rec.headers).not.toHaveProperty('set-cookie');
});

test('the whole plan is sent and reported, then done', async () => {
  const fetchImpl = jest.fn().mockResolvedValue(reply(200, { usage: { total_tokens: 3 } }));
  const flushed: BrowserRecord[] = [];
  const done: string[] = [];
  const reason = await runBurst(
    order(),
    new AbortController().signal,
    {
      flush: async (records, d) => {
        flushed.push(...records);
        if (d) done.push(d.reason);
        return true;
      },
    },
    deps(fetchImpl),
  );
  expect(reason).toBe('finished');
  expect(flushed).toHaveLength(3);
  expect(fetchImpl.mock.calls.map((c) => c[1].headers.Authorization)).toEqual(
    expect.arrayContaining(['Bearer sk-oai-1', 'Bearer sk-oai-2']),
  );
  expect(done).toEqual(['finished']);
});

test('no readable answer at all stops after the first request: blocked', async () => {
  const fetchImpl = jest.fn().mockRejectedValue(new TypeError('Failed to fetch'));
  const flushed: BrowserRecord[] = [];
  let doneWith: { reason: string; detail: string } | undefined;
  const reason = await runBurst(
    order({ retries: 0 }),
    new AbortController().signal,
    {
      flush: async (records, d) => {
        flushed.push(...records);
        doneWith = d ?? doneWith;
        return true;
      },
    },
    deps(fetchImpl),
  );
  expect(reason).toBe('blocked');
  expect(fetchImpl).toHaveBeenCalledTimes(1);
  expect(flushed[0].error).toContain('Blocked by the browser: no readable answer from maas.test');
  expect(doneWith).toEqual({ reason: 'blocked', detail: 'TypeError: Failed to fetch' });
});

test('stops after the configured number of 429s', async () => {
  const fetchImpl = jest.fn().mockResolvedValue(reply(429, ''));
  const flushed: BrowserRecord[] = [];
  await runBurst(
    order({ concurrency: 1, stop_after_429s: 2, plan: [0, 0, 0, 0, 0] }),
    new AbortController().signal,
    { flush: async (records) => (flushed.push(...records), true) },
    deps(fetchImpl),
  );
  expect(flushed).toHaveLength(2);
});

test('stops sending once the step is over for this tab', async () => {
  // Real fetch yields to the event loop, which is when the flush timer runs.
  const fetchImpl = jest.fn(() => new Promise((resolve) => setTimeout(() => resolve(reply(200, {})), 1)));
  const reason = await runBurst(
    order({ concurrency: 1, plan: Array(50).fill(0) }),
    new AbortController().signal,
    { flush: async () => false },
    { ...deps(fetchImpl), flushMs: 0 },
  );
  expect(reason).toBe('finished');
  expect(fetchImpl.mock.calls.length).toBeLessThan(50);
});
