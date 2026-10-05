// "Send from user browser": runs one send-requests step from this tab, straight
// to the MaaS gateway, and reports one raw record per request back to the
// harness (api/routes/browser.py → the Job tallies them with the same code as
// requests it sends itself). Mirrors bff/harness/tasks/inference.py's
// _send_one: same request bodies, the OpenAI SDK's retry rules, streamed
// replies read to the end with a final usage chunk.
import type { BrowserDoneReason, BrowserOrder, BrowserRecord, BrowserTarget, RequestApi } from './api/client';

const MAX_BODY_CHARS = 2000;
const MAX_ARRIVALS = 20000;
const MAX_BATCH = 500;
export const FLUSH_MS = 500;
// The token-usage fields the harness reads (chat/completions/embeddings and
// the Responses API's input/output naming).
const USAGE_FIELDS = ['prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens'];
// The response headers failure_origin() reads — visible here only if the
// gateway exposes them to cross-origin callers.
const KEPT_HEADERS = ['server', 'x-ext-auth-reason'];

// The OpenAI SDK retries these (and connection errors).
const retryable = (status: number): boolean => status === 408 || status === 409 || status === 429 || status >= 500;

export function requestBody(
  order: Pick<BrowserOrder, 'api' | 'stream' | 'prompt' | 'batch_size'>,
  model: string,
): Record<string, unknown> {
  const many = order.batch_size > 1 ? Array<string>(order.batch_size).fill(order.prompt) : order.prompt;
  const streamed = { stream: true, stream_options: { include_usage: true } };
  switch (order.api) {
    case 'embeddings':
      return { model, input: many };
    case 'completions':
      return order.stream ? { model, prompt: many, ...streamed } : { model, prompt: many };
    case 'responses':
      return order.stream ? { model, input: order.prompt, stream: true } : { model, input: order.prompt };
    default: {
      const messages = [{ role: 'user', content: order.prompt }];
      return order.stream ? { model, messages, ...streamed } : { model, messages };
    }
  }
}

// Server-sent events → each event's `data:` payload, across chunk boundaries.
export class SseParser {
  private buf = '';

  push(text: string): string[] {
    this.buf += text.replace(/\r/g, '');
    const out: string[] = [];
    let end = this.buf.indexOf('\n\n');
    while (end >= 0) {
      const event = this.buf.slice(0, end);
      this.buf = this.buf.slice(end + 2);
      const data = event
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).replace(/^ /, ''))
        .join('\n');
      if (data) out.push(data);
      end = this.buf.indexOf('\n\n');
    }
    return out;
  }
}

function numericUsage(usage: unknown): Record<string, number> | undefined {
  if (!usage || typeof usage !== 'object') return undefined;
  const out: Record<string, number> = {};
  for (const k of USAGE_FIELDS) {
    const v = (usage as Record<string, unknown>)[k];
    if (typeof v === 'number') out[k] = Math.round(v);
  }
  return Object.keys(out).length ? out : undefined;
}

// What one streamed event carries: generated text, and/or the final usage.
export function readStreamEvent(api: RequestApi, data: string): { text: boolean; usage?: Record<string, number> } {
  if (data === '[DONE]') return { text: false };
  let obj: Record<string, unknown>;
  try {
    obj = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return { text: false };
  }
  if (api === 'responses') {
    if (obj.type === 'response.output_text.delta') return { text: true };
    if (obj.type === 'response.completed') {
      return { text: false, usage: numericUsage((obj.response as Record<string, unknown> | undefined)?.usage) };
    }
    return { text: false };
  }
  const choice = (obj.choices as Record<string, unknown>[] | undefined)?.[0];
  const text =
    api === 'completions'
      ? !!choice?.text
      : !!(choice?.delta as Record<string, unknown> | undefined)?.content;
  return { text, usage: numericUsage(obj.usage) };
}

export interface SendDeps {
  fetchImpl?: typeof fetch;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// The SDK's backoff: Retry-After when the gateway exposes it (≤ 60 s), else
// 0.5 s doubling up to 8 s, with jitter.
function retryDelayMs(response: Response | null, attempt: number): number {
  const header = response?.headers.get('retry-after');
  const seconds = header ? Number(header) : NaN;
  if (Number.isFinite(seconds) && seconds >= 0 && seconds <= 60) return seconds * 1000;
  return Math.min(0.5 * 2 ** attempt, 8) * (1 - 0.25 * Math.random()) * 1000;
}

function readableHeaders(response: Response): Record<string, string> {
  const out: Record<string, string> = {};
  for (const name of KEPT_HEADERS) {
    const v = response.headers.get(name);
    if (v) out[name] = v;
  }
  return out;
}

async function readReply(
  response: Response,
  order: BrowserOrder,
  t0: number,
  now: () => number,
): Promise<{ usage?: Record<string, number>; arrivals: number[] }> {
  if (!order.stream || !response.body) {
    const json = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    return { usage: numericUsage(json.usage), arrivals: [] };
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const arrivals: number[] = [];
  let usage: Record<string, number> | undefined;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const data of parser.push(decoder.decode(value, { stream: true }))) {
      const event = readStreamEvent(order.api, data);
      if (event.text && arrivals.length < MAX_ARRIVALS) arrivals.push(now() - t0);
      if (event.usage) usage = event.usage;
    }
  }
  return { usage, arrivals };
}

// One request, retries included, as the record the harness tallies.
export async function sendOne(
  order: BrowserOrder,
  target: BrowserTarget,
  signal: AbortSignal,
  burstStart: number,
  deps: SendDeps = {},
): Promise<BrowserRecord> {
  const now = deps.now ?? (() => performance.now());
  const doFetch = deps.fetchImpl ?? fetch;
  const sleep = deps.sleep ?? defaultSleep;
  const t0 = now();
  const base = { t0_offset_s: Math.max(0, (t0 - burstStart) / 1000) };
  const attempts: NonNullable<BrowserRecord['attempts']> = [];
  const url = target.url.replace(/\/+$/, '') + order.path;
  const body = JSON.stringify(requestBody(order, target.model));

  for (let attempt = 0; ; attempt++) {
    const attemptStart = now();
    const timeout = new AbortController();
    const onAbort = () => timeout.abort();
    signal.addEventListener('abort', onAbort);
    const timer = order.timeout_s ? setTimeout(() => timeout.abort(), order.timeout_s * 1000) : undefined;
    try {
      let response: Response;
      try {
        response = await doFetch(url, {
          method: 'POST',
          headers: { Authorization: `Bearer ${target.key}`, 'Content-Type': 'application/json' },
          body,
          signal: timeout.signal,
          // The dashboard's cookies are not MaaS's business.
          credentials: 'omit',
          cache: 'no-store',
        });
      } catch (err) {
        if (signal.aborted) throw err;
        if (attempt < order.retries) {
          await sleep(retryDelayMs(null, attempt));
          continue;
        }
        const e = err as Error;
        const error = timeout.signal.aborted
          ? `Timeout: no answer within ${order.timeout_s}s`
          : `${e.name || 'Error'}: ${e.message}`;
        return { ...base, latency_ms: now() - t0, error, attempts };
      }
      attempts.push({ status: response.status, reason: response.statusText, ms: now() - attemptStart });
      if (!response.ok) {
        if (retryable(response.status) && attempt < order.retries) {
          await response.body?.cancel().catch(() => undefined);
          await sleep(retryDelayMs(response, attempt));
          continue;
        }
        const text = (await response.text().catch(() => '')).slice(0, MAX_BODY_CHARS);
        return {
          ...base,
          latency_ms: now() - t0,
          ok: false,
          status: response.status,
          reason: response.statusText,
          body: text,
          headers: readableHeaders(response),
          attempts,
        };
      }
      try {
        const reply = await readReply(response, order, t0, now);
        return {
          ...base,
          latency_ms: now() - t0,
          ok: true,
          status: response.status,
          usage: reply.usage ?? null,
          arrivals: reply.arrivals,
          attempts,
        };
      } catch (err) {
        if (signal.aborted) throw err;
        const e = err as Error;
        // The answer started but broke off — no complete reply.
        return { ...base, latency_ms: now() - t0, error: `${e.name || 'Error'} while reading the reply: ${e.message}`, attempts };
      }
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
    }
  }
}

export function blockedMessage(url: string, error: string | null | undefined): string {
  let host = url;
  try {
    host = new URL(url).host;
  } catch {
    // keep the raw URL
  }
  // A short, groupable error reason, like the harness's own — the step's
  // finding and verdict explain what it means (CORS, or unreachable).
  return `Blocked by the browser: no readable answer from ${host}` + (error ? ` (${error})` : '');
}

export interface BurstCallbacks {
  // Report records (and, last, the done marker). false = the step is over for this tab.
  flush: (records: BrowserRecord[], done?: { reason: BrowserDoneReason; detail: string }) => Promise<boolean>;
  onProgress?: (finished: number, total: number) => void;
}

// The whole step: the first request alone (if the browser can't read any
// answer at all, that's the result — no point sending the rest), then the
// remaining plan with `concurrency` in flight. Records go back every
// FLUSH_MS; returns why it ended.
export async function runBurst(
  order: BrowserOrder,
  signal: AbortSignal,
  cb: BurstCallbacks,
  deps: SendDeps & { flushMs?: number } = {},
): Promise<BrowserDoneReason> {
  const now = deps.now ?? (() => performance.now());
  const inner = new AbortController();
  const onOuterAbort = () => inner.abort();
  signal.addEventListener('abort', onOuterAbort);
  const burstStart = now();
  const total = order.plan.length;
  let pending: BrowserRecord[] = [];
  let finished = 0;
  let throttled = 0;
  let transport = 0;
  let gone = false;

  const flush = async (done?: { reason: BrowserDoneReason; detail: string }): Promise<void> => {
    if (gone) return;
    const batch = pending;
    pending = [];
    if (!batch.length && !done) return;
    for (let i = 0; i < Math.max(batch.length, 1); i += MAX_BATCH) {
      const chunk = batch.slice(i, i + MAX_BATCH);
      const last = i + MAX_BATCH >= batch.length;
      let ok: boolean;
      try {
        ok = await cb.flush(chunk, last ? done : undefined);
      } catch {
        // The BFF didn't answer — keep the rest for the next flush.
        pending = [...batch.slice(i), ...pending];
        return;
      }
      if (!ok) {
        gone = true;
        inner.abort();
        return;
      }
    }
  };
  let flushing: Promise<void> = Promise.resolve();
  const timer = setInterval(() => {
    flushing = flushing.then(() => flush());
  }, deps.flushMs ?? FLUSH_MS);

  const record = (r: BrowserRecord) => {
    pending.push(r);
    finished += 1;
    if (r.status === 429) throttled += 1;
    if (r.status == null) transport += 1;
    cb.onProgress?.(finished, total);
  };
  const stopNow = () =>
    gone ||
    inner.signal.aborted ||
    (order.stop_after_429s > 0 && throttled >= order.stop_after_429s) ||
    (order.stop_after_transport_errors > 0 && transport >= order.stop_after_transport_errors);

  let reason: BrowserDoneReason = 'finished';
  let detail = '';
  try {
    if (total > 0) {
      const firstTarget = order.targets[order.plan[0]];
      const first = await sendOne(order, firstTarget, inner.signal, burstStart, deps);
      if (first.status == null) {
        detail = first.error ?? '';
        first.error = blockedMessage(firstTarget.url, first.error);
        reason = 'blocked';
        record(first);
      } else {
        record(first);
        let next = 1;
        const worker = async () => {
          while (next < total && !stopNow()) {
            const idx = next++;
            record(await sendOne(order, order.targets[order.plan[idx]], inner.signal, burstStart, deps));
          }
        };
        await Promise.all(Array.from({ length: Math.max(1, order.concurrency) }, worker));
      }
    }
  } catch (err) {
    if (!inner.signal.aborted) {
      reason = 'error';
      detail = String(err);
    }
  }
  if (signal.aborted) reason = 'stopped';
  clearInterval(timer);
  signal.removeEventListener('abort', onOuterAbort);
  await flushing;
  await flush({ reason, detail });
  return reason;
}
