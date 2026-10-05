import { useEffect, useRef, useState } from 'react';
import { Button, Label, Spinner } from '@patternfly/react-core';
import { Table, Tbody, Td, Th, Thead, Tr } from '@patternfly/react-table';
import { listRuns, stopRun, type Run } from '../api/client';
import { useScenarioTitle } from '../scenarioTitles';

const ACTIVE_STATUSES = new Set(['PENDING', 'RUNNING']);

function statusColor(status: string): 'green' | 'red' | 'blue' | 'grey' | 'orange' {
  const u = status.toUpperCase();
  if (u === 'PASS') return 'green';
  if (u === 'FAIL') return 'red';
  if (u === 'CANCELLED') return 'orange';
  if (u === 'RUNNING') return 'blue';
  return 'grey';
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function StatusBadge({ status }: { status: string }) {
  return (
    <Label
      color={statusColor(status)}
      style={{ fontWeight: 700, fontSize: '0.72rem', letterSpacing: '0.04em' }}
    >
      {status.toUpperCase()}
    </Label>
  );
}

interface Props {
  onViewRun: (runId: string) => void;
}

export function RunHistory({ onViewRun }: Props) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const titleFor = useScenarioTitle();

  function stopPolling() {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }

  async function fetchRuns() {
    try {
      const data = await listRuns();
      setRuns(data);
      setLoading(false);
      const hasActive = data.some((r) => ACTIVE_STATUSES.has(r.status.toUpperCase()));
      if (!hasActive) stopPolling();
    } catch {
      setLoading(false);
    }
  }

  async function handleStop(runId: string) {
    if (!window.confirm('Stop this run? Already-running work will be cancelled.')) return;
    try {
      await stopRun(runId);
      void fetchRuns();
    } catch (err) {
      console.error(err);
    }
  }

  useEffect(() => {
    void fetchRuns();
    intervalRef.current = setInterval(() => { void fetchRuns(); }, 3000);
    return () => stopPolling();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) return <Spinner aria-label="Loading run history" />;

  return (
    <>
      <p className="maaspal-section-heading">Run History</p>
      <Table aria-label="Run history">
        <Thead>
          <Tr>
            <Th>Run ID</Th>
            <Th>Scenario</Th>
            <Th>Status</Th>
            <Th>Started</Th>
            <Th>Duration</Th>
            <Th />
          </Tr>
        </Thead>
        <Tbody>
          {runs.length === 0 ? (
            <Tr>
              <Td colSpan={6} style={{ color: '#888', fontStyle: 'italic' }}>
                No runs yet.
              </Td>
            </Tr>
          ) : (
            runs.map((r) => {
              const isActive = ACTIVE_STATUSES.has(r.status.toUpperCase());
              return (
                <Tr key={r.id}>
                  <Td>
                    <span title={r.id} style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>
                      {r.id.slice(0, 8)}
                    </span>
                  </Td>
                  <Td>{titleFor(r.scenario)}</Td>
                  <Td><StatusBadge status={r.status} /></Td>
                  <Td style={{ fontSize: '0.85rem', color: '#555' }}>
                    {new Date(r.created_at).toLocaleString()}
                  </Td>
                  <Td style={{ fontSize: '0.85rem', color: '#555' }}>
                    {!isActive && typeof r.duration_ms === 'number' ? formatDuration(r.duration_ms) : '—'}
                  </Td>
                  <Td>
                    <Button variant="link" isInline onClick={() => onViewRun(r.id)}>
                      View
                    </Button>
                    {isActive && (
                      <Button
                        variant="link"
                        isInline
                        style={{ color: '#c62828', marginLeft: '0.75rem' }}
                        onClick={() => void handleStop(r.id)}
                      >
                        Stop
                      </Button>
                    )}
                  </Td>
                </Tr>
              );
            })
          )}
        </Tbody>
      </Table>
    </>
  );
}
