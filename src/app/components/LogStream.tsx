import { useCallback, useEffect, useRef, useState } from 'react';
import { CodeBlock, CodeBlockCode } from '@patternfly/react-core';
import { API_BASE } from '../api/client';

type StreamStatus = 'connecting' | 'streaming' | 'completed' | 'error';

const STATUS_LABEL: Record<StreamStatus, string> = {
  connecting: 'Connecting',
  streaming: 'Streaming',
  completed: 'Completed',
  error: 'Error',
};

const POLL_INTERVAL_MS = 1000;

interface LogPollResponse {
  lines: string[];
  done: boolean;
  next_offset: number;
}

interface Props {
  runId: string;
  /** Called whenever the captured lines change — lets a collapsed logs panel
   * show a line count and last-line preview without rendering the log. */
  onLines?: (lines: string[]) => void;
}

export function LogStream({ runId, onLines }: Props) {
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    onLines?.(lines);
  }, [lines, onLines]);
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const [newLineCount, setNewLineCount] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const isAtBottom = useRef(true);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      isAtBottom.current = true;
      setNewLineCount(0);
    }
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    isAtBottom.current = atBottom;
    if (atBottom) setNewLineCount(0);
  }, []);

  useEffect(() => {
    setLines([]);
    setStatus('connecting');
    setNewLineCount(0);
    isAtBottom.current = true;

    let active = true;
    let offset = 0;

    async function poll() {
      let consecutiveErrors = 0;
      while (active) {
        try {
          const res = await fetch(`${API_BASE}/runs/${runId}/logs/lines?offset=${offset}`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);

          const data = (await res.json()) as LogPollResponse;
          consecutiveErrors = 0;

          if (data.lines.length > 0) {
            setStatus('streaming');
            const count = data.lines.length;
            setLines((prev) => [...prev, ...data.lines]);
            offset = data.next_offset;
            if (!isAtBottom.current) {
              setNewLineCount((n) => n + count);
            }
          }

          if (data.done) {
            setStatus('completed');
            return;
          }
        } catch {
          consecutiveErrors++;
          if (consecutiveErrors >= 10) {
            setStatus('error');
            return;
          }
        }

        await new Promise<void>((r) => setTimeout(r, POLL_INTERVAL_MS));
      }
    }

    void poll();
    return () => {
      active = false;
    };
  }, [runId]);

  // Auto-scroll only when user is at the bottom.
  useEffect(() => {
    if (isAtBottom.current) {
      scrollToBottom();
    }
  }, [lines, scrollToBottom]);

  return (
    <div className="maaspal-log-block">
      <div className={`maaspal-stream-status maaspal-stream-status--${status}`}>
        <span className="maaspal-stream-status__dot" />
        <span>{STATUS_LABEL[status]}</span>
        {lines.length > 0 && (
          <span style={{ marginLeft: 'auto', fontWeight: 400, fontSize: '0.75rem', color: '#888' }}>
            {lines.length} lines
          </span>
        )}
      </div>
      <div style={{ position: 'relative' }}>
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          style={{ maxHeight: '420px', overflowY: 'auto' }}
        >
          <CodeBlock>
            <CodeBlockCode>{lines.join('\n') || '(waiting for logs…)'}</CodeBlockCode>
          </CodeBlock>
        </div>
        {newLineCount > 0 && (
          <button className="maaspal-log-newlines-badge" onClick={scrollToBottom}>
            ↓ {newLineCount} new {newLineCount === 1 ? 'line' : 'lines'}
          </button>
        )}
      </div>
    </div>
  );
}
