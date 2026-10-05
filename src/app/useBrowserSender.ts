import { useEffect, useState } from 'react';
import { claimBrowserWork, getBrowserWork, postBrowserResults } from './api/client';
import { runBurst } from './browserSender';

const POLL_MS = 1000;

export type BrowserSendState =
  | { phase: 'idle' }
  | { phase: 'sending'; step: string; finished: number; total: number }
  // Another tab (or window) took the step.
  | { phase: 'elsewhere'; step: string };

// While a run is active, picks up any step it hands to the browser ("Send from
// user browser") and sends it from this tab. Leaving the page, or the run
// ending (Stop), aborts whatever is in flight.
export function useBrowserSender(runId: string, active: boolean): BrowserSendState {
  const [state, setState] = useState<BrowserSendState>({ phase: 'idle' });

  useEffect(() => {
    if (!active) {
      setState({ phase: 'idle' });
      return;
    }
    const controller = new AbortController();
    let busy = false;

    const warnBeforeLeaving = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };

    const tick = async () => {
      if (busy || controller.signal.aborted) return;
      busy = true;
      try {
        const work = await getBrowserWork(runId);
        if (!work.pending) {
          setState({ phase: 'idle' });
          return;
        }
        if (work.claimed) {
          setState({ phase: 'elsewhere', step: work.step ?? '' });
          return;
        }
        const order = await claimBrowserWork(runId, window.location.origin);
        if (!order) return;
        setState({ phase: 'sending', step: order.step, finished: 0, total: order.plan.length });
        window.addEventListener('beforeunload', warnBeforeLeaving);
        try {
          await runBurst(order, controller.signal, {
            flush: (records, done) =>
              postBrowserResults(runId, {
                claim_id: order.claim_id,
                result_key: order.result_key,
                records,
                ...(done ? { done: true, reason: done.reason, detail: done.detail } : {}),
              }),
            onProgress: (finished, total) =>
              setState({ phase: 'sending', step: order.step, finished, total }),
          });
        } finally {
          window.removeEventListener('beforeunload', warnBeforeLeaving);
        }
        if (!controller.signal.aborted) setState({ phase: 'idle' });
      } catch {
        // The BFF was unreachable for a moment — the next tick tries again.
      } finally {
        busy = false;
      }
    };

    void tick();
    const id = setInterval(() => void tick(), POLL_MS);
    return () => {
      clearInterval(id);
      controller.abort();
      window.removeEventListener('beforeunload', warnBeforeLeaving);
    };
  }, [runId, active]);

  return state;
}
