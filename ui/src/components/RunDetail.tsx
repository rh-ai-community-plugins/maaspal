import { lazy, Suspense, useEffect, useState } from 'react';
import { Button, Grid, GridItem, Page, PageSection, Spinner, Switch, Tooltip } from '@patternfly/react-core';
import { AssertionPanel } from './AssertionPanel';
import { LogStream } from './LogStream';
import { DetailTable, ResourcesPanel, TrafficPanel, VerdictBanner } from './RunInsights';
import { TaskProgress } from './TaskProgress';
import { useScenarioTitle } from '../scenarioTitles';
import {
  cleanupRun,
  getAssertions,
  getProgress,
  getRun,
  setAutoCleanup,
  stopRun,
  type AssertionState,
  type ProgressResponse,
  type Run,
  type TaskProgressEntry,
} from '../api/client';

const ACTIVE_STATUSES = new Set(['PENDING', 'RUNNING']);

// Code-split: RunSettingsModal pulls in Monaco (self-hosted, see monacoSetup.ts),
// a sizeable bundle not worth loading for every run view when most never open it.
const RunSettingsModal = lazy(() =>
  import('./RunSettingsModal').then((m) => ({ default: m.RunSettingsModal }))
);

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function StatusDot({ status }: { status: string }) {
  const upper = status.toUpperCase();
  const color =
    upper === 'PASS' ? '#2e7d32'
    : upper === 'FAIL' ? '#c62828'
    : upper === 'CANCELLED' ? '#b26a00'
    : upper === 'RUNNING' ? '#1565c0'
    : '#9e9e9e';
  return (
    <span
      style={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        background: color,
        marginRight: '0.4rem',
        verticalAlign: 'middle',
      }}
    />
  );
}

interface Props {
  runId: string;
  onBack: () => void;
}

export function RunDetail({ runId, onBack }: Props) {
  const [run, setRun] = useState<Run | null>(null);
  const [assertions, setAssertions] = useState<AssertionState[]>([]);
  const [taskProgress, setTaskProgress] = useState<TaskProgressEntry[]>([]);
  const [runStartedAt, setRunStartedAt] = useState<string | undefined>(undefined);
  const [insights, setInsights] = useState<Omit<ProgressResponse, 'tasks' | 'run_started_at'>>({});
  const titleFor = useScenarioTitle();
  const [stopping, setStopping] = useState(false);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [, setTick] = useState(0);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, []);

  // Re-render once a second so the total elapsed time visibly ticks up between
  // the 4s /api/runs/{id} polls below, without the backend pushing a live number.
  useEffect(() => {
    if (!run || !ACTIVE_STATUSES.has(run.status.toUpperCase())) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [run]);

  useEffect(() => {
    getRun(runId).then(setRun).catch(console.error);
    const interval = setInterval(() => {
      getRun(runId)
        .then((r) => {
          setRun(r);
          if (r.status !== 'RUNNING' && r.status !== 'PENDING') {
            clearInterval(interval);
          }
        })
        .catch(console.error);
    }, 4000);
    return () => clearInterval(interval);
  }, [runId]);

  useEffect(() => {
    getAssertions(runId).then(setAssertions).catch(() => {});
    const interval = setInterval(() => {
      getAssertions(runId).then(setAssertions).catch(() => {});
    }, 2000);
    return () => clearInterval(interval);
  }, [runId]);

  useEffect(() => {
    function applyProgress(p: ProgressResponse) {
      setTaskProgress(p.tasks);
      if (p.run_started_at) setRunStartedAt(p.run_started_at);
      setInsights({ traffic: p.traffic, resources: p.resources, tables: p.tables, verdict: p.verdict });
    }
    getProgress(runId).then(applyProgress).catch(() => {});
    const interval = setInterval(() => {
      getProgress(runId).then(applyProgress).catch(() => {});
    }, 2000);
    return () => clearInterval(interval);
  }, [runId]);

  async function handleStop() {
    if (!window.confirm('Stop this run? Already-running work will be cancelled.')) return;
    setStopping(true);
    try {
      await stopRun(runId);
      getRun(runId).then(setRun).catch(() => {});
    } catch (err) {
      console.error(err);
    } finally {
      setStopping(false);
    }
  }

  async function handleAutoCleanupToggle(enabled: boolean) {
    setRun((prev) => (prev ? { ...prev, auto_cleanup: enabled } : prev)); // optimistic
    try {
      await setAutoCleanup(runId, enabled);
    } catch (err) {
      console.error(err);
      getRun(runId).then(setRun).catch(() => {}); // reconcile on failure
    }
  }

  async function handleCleanupNow() {
    setCleaningUp(true);
    try {
      await cleanupRun(runId);
      getRun(runId).then(setRun).catch(() => {});
    } catch (err) {
      console.error(err);
    } finally {
      setCleaningUp(false);
    }
  }

  return (
    <Page>
      <PageSection>
        <div className="maaspal-run-detail-bar">
          <Button variant="link" isInline onClick={onBack}>
            ← Back
          </Button>

          {run ? (
            <div className="maaspal-run-detail-meta">
              <span className="maaspal-run-detail-meta__item">
                <strong>{titleFor(run.scenario)}</strong>
              </span>
              <span className="maaspal-run-detail-meta__item">
                <StatusDot status={run.status} />
                <strong>{run.status}</strong>
              </span>
              <span className="maaspal-run-detail-meta__item">
                Started: <strong>{new Date(run.created_at).toLocaleString()}</strong>
              </span>
              {(() => {
                const isActive = ACTIVE_STATUSES.has(run.status.toUpperCase());
                const durationMs = isActive
                  ? runStartedAt
                    ? Date.now() - new Date(runStartedAt).getTime()
                    : null
                  : (run.duration_ms ?? null);
                return durationMs !== null ? (
                  <span className="maaspal-run-detail-meta__item">
                    Duration: <strong>{formatDuration(durationMs)}</strong>
                  </span>
                ) : null;
              })()}
              <span
                className="maaspal-run-detail-meta__item"
                title={runId}
                style={{ fontFamily: 'monospace', fontSize: '0.78rem', color: '#aaa' }}
              >
                {runId.slice(0, 12)}…
              </span>
              <Button variant="link" isInline onClick={() => setShowSettings(true)}>
                View Settings
              </Button>
              {(() => {
                const isActive = ACTIVE_STATUSES.has(run.status.toUpperCase());
                const switchEl = (
                  <Switch
                    id="run-auto-cleanup"
                    label="Auto cleanup"
                    isChecked={run.auto_cleanup}
                    isDisabled={!isActive}
                    onChange={(_e, checked) => void handleAutoCleanupToggle(checked)}
                  />
                );
                return (
                  <span className="maaspal-run-detail-meta__item">
                    {isActive ? (
                      switchEl
                    ) : (
                      <Tooltip content="Auto cleanup can only be changed while a run is active">
                        <span>{switchEl}</span>
                      </Tooltip>
                    )}
                  </span>
                );
              })()}
              {ACTIVE_STATUSES.has(run.status.toUpperCase()) && (
                <Button
                  variant="danger"
                  isInline
                  isLoading={stopping}
                  isDisabled={stopping}
                  onClick={() => void handleStop()}
                >
                  Stop
                </Button>
              )}
              {!ACTIVE_STATUSES.has(run.status.toUpperCase()) && run.cleanup_status === 'cleaning' && (
                <span className="maaspal-run-detail-meta__item">
                  <Spinner size="sm" aria-label="Cleaning up" /> Cleaning up…
                </span>
              )}
              {!ACTIVE_STATUSES.has(run.status.toUpperCase()) &&
                (run.cleanup_status === 'skipped' || run.cleanup_status === 'failed') && (
                  <span className="maaspal-run-detail-meta__item">
                    <Button
                      variant="secondary"
                      isInline
                      isLoading={cleaningUp}
                      isDisabled={cleaningUp}
                      onClick={() => void handleCleanupNow()}
                    >
                      Clean Up Now
                    </Button>
                    {run.cleanup_status === 'failed' && run.cleanup_error && (
                      <Tooltip content={run.cleanup_error}>
                        <span style={{ color: '#c62828', marginLeft: '0.4rem' }}>cleanup failed ⓘ</span>
                      </Tooltip>
                    )}
                  </span>
                )}
            </div>
          ) : (
            <Spinner size="sm" aria-label="Loading run" />
          )}
        </div>

        {insights.verdict && <VerdictBanner verdict={insights.verdict} />}

        <TaskProgress tasks={taskProgress} />

        <Grid hasGutter>
          <GridItem span={12} lg={8}>
            {(insights.traffic ?? []).map((burst) => (
              <TrafficPanel key={burst.result_key} burst={burst} />
            ))}
            {(insights.tables ?? []).map((table) => (
              <DetailTable key={table.title} table={table} />
            ))}
            <p className="maaspal-section-heading">Live Logs</p>
            <LogStream runId={runId} />
          </GridItem>
          <GridItem span={12} lg={4}>
            <AssertionPanel assertions={assertions} taskProgress={taskProgress} />
            <div style={{ marginTop: '1rem' }}>
              <ResourcesPanel resources={insights.resources ?? []} cleanupStatus={run?.cleanup_status} />
            </div>
          </GridItem>
        </Grid>
      </PageSection>

      {showSettings && (
        <Suspense fallback={<Spinner size="lg" aria-label="Loading editor" />}>
          <RunSettingsModal runId={runId} onClose={() => setShowSettings(false)} />
        </Suspense>
      )}
    </Page>
  );
}
