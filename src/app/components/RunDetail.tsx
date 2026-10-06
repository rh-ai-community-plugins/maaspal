import { lazy, Suspense, useEffect, useState } from 'react';
import { Alert, Button, PageSection, Spinner, Switch, Tooltip } from '@patternfly/react-core';
import { AssertionPanel } from './AssertionPanel';
import { LogStream } from './LogStream';
import {
  DetailTable,
  FindingsPanel,
  MetricsChartsPanel,
  RunSteps,
  TrafficGroupPanel,
  TrafficPanel,
  VerdictBanner,
} from './RunInsights';
import { TaskProgress } from './TaskProgress';
import { formatTaskName, useScenarioTitle } from '../scenarioTitles';
import { useBrowserSender, type BrowserSendState } from '../useBrowserSender';
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
  type TrafficBurst,
} from '../api/client';
import { statusStyle } from '../status';
import { COLOR, toneColor } from '../styles/colors';

const ACTIVE_STATUSES = new Set(['PENDING', 'RUNNING']);

// "Send from user browser": this tab is sending a step's requests (or another
// tab is) — closing it would leave the step without a sender.
function BrowserSendBanner({ state }: { state: BrowserSendState }) {
  if (state.phase === 'idle') return null;
  if (state.phase === 'elsewhere') {
    return (
      <Alert
        variant="info"
        isInline
        isPlain
        title={`${formatTaskName(state.step)} is being sent from another browser tab — keep that tab open.`}
      />
    );
  }
  return (
    <Alert
      variant="info"
      isInline
      title={`Sending from your browser: ${state.finished} / ${state.total} requests for ${formatTaskName(state.step)}`}
    >
      These requests go from this browser straight to the MaaS gateway. Keep this page open until the step
      finishes.
    </Alert>
  );
}
// Cleanup keeps going after a run reaches its final status — keep polling the
// run until cleanup is settled too, or resource statuses go stale.
const CLEANUP_SETTLED = new Set(['done', 'failed', 'skipped']);

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

/** Bursts in the same chart_group are shown together on one timeline; the
 * rest stand alone. Charted ones first — they answer the scenario's question. */
function groupTraffic(traffic: TrafficBurst[]): TrafficBurst[][] {
  const groups = new Map<string, TrafficBurst[]>();
  const alone: TrafficBurst[][] = [];
  for (const b of traffic) {
    if (b.chart_group) groups.set(b.chart_group, [...(groups.get(b.chart_group) ?? []), b]);
    else alone.push([b]);
  }
  const charted = (g: TrafficBurst[]) => g.some((b) => b.chart || b.summary.stages?.length);
  return [...groups.values(), ...alone].sort((a, b) => Number(charted(b)) - Number(charted(a)));
}

/** Logs as a clearly toggleable panel: line count and the latest line while
 * collapsed, the full stream when open (always opened for a failed run). */
function LogsPanel({ runId, failed }: { runId: string; failed: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const isOpen = open ?? failed;
  return (
    <section className="maaspal-panel maaspal-logs-panel" aria-label="Logs">
      <div className="maaspal-panel__header">
        <p className="maaspal-panel__title">
          Logs{' '}
          <span className="maaspal-stat__sub">
            · {lines.length.toLocaleString()} {lines.length === 1 ? 'line' : 'lines'}
          </span>
        </p>
        <Button variant="secondary" size="sm" onClick={() => setOpen(!isOpen)} aria-expanded={isOpen}>
          {isOpen ? 'Hide logs' : 'Show logs'}
        </Button>
      </div>
      {!isOpen && lines.length > 0 && (
        <code className="maaspal-logs-panel__preview" title={lines[lines.length - 1]}>
          {lines[lines.length - 1]}
        </code>
      )}
      {/* Stays mounted while hidden so the count and preview keep updating. */}
      <div hidden={!isOpen}>
        <LogStream runId={runId} onLines={setLines} />
      </div>
    </section>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = toneColor(statusStyle(status).tone);
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
  const [selectedView, setSelectedView] = useState<string>('overview');
  const [stopping, setStopping] = useState(false);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [, setTick] = useState(0);
  const browserSend = useBrowserSender(runId, run?.status.toUpperCase() === 'RUNNING');

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
          if (!ACTIVE_STATUSES.has(r.status.toUpperCase()) && CLEANUP_SETTLED.has(r.cleanup_status)) {
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
      setInsights({
        traffic: p.traffic,
        resources: p.resources,
        tables: p.tables,
        verdict: p.verdict,
        findings: p.findings,
        metrics_charts: p.metrics_charts,
      });
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
    <>
      <PageSection>
        <div className="maaspal-run-detail-bar">
          <Button variant="link" isInline onClick={onBack} className="maaspal-run-detail-back">
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
                style={{ fontFamily: 'monospace', fontSize: '0.78rem', color: COLOR.muted }}
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
                        <span style={{ color: toneColor('danger'), marginLeft: '0.4rem' }}>cleanup failed ⓘ</span>
                      </Tooltip>
                    )}
                  </span>
                )}
            </div>
          ) : (
            <Spinner size="sm" aria-label="Loading run" />
          )}
        </div>

        <BrowserSendBanner state={browserSend} />

        <TaskProgress tasks={taskProgress} selectedView={selectedView} onSelect={setSelectedView} />

        {selectedView === 'overview' ? (
          <>
            {(insights.findings ?? []).length > 0 ? (
              <FindingsPanel findings={insights.findings ?? []} />
            ) : (
              insights.verdict && <VerdictBanner verdict={insights.verdict} />
            )}
            <RunSteps
              tasks={taskProgress}
              resources={insights.resources ?? []}
              cleanupStatus={run?.cleanup_status}
              traffic={insights.traffic ?? []}
            />
            <MetricsChartsPanel charts={insights.metrics_charts ?? []} />
            {(insights.tables ?? []).map((table) => (
              <DetailTable key={table.title} table={table} />
            ))}
          </>
        ) : (
          <>
            {(() => {
              const taskAssertions = assertions.filter((a) => a.task === selectedView);
              const taskTraffic = groupTraffic(
                (insights.traffic ?? []).filter((b) => b.task === selectedView),
              );
              const selectedTask = taskProgress.find((t) => t.name === selectedView);
              const isActive =
                selectedTask?.status === 'RUNNING' || selectedTask?.status === 'PENDING';
              const trafficNodes = taskTraffic.map((group) =>
                group.length > 1 ? (
                  <TrafficGroupPanel key={group[0].chart_group ?? group[0].result_key} bursts={group} />
                ) : (
                  <TrafficPanel key={group[0].result_key} burst={group[0]} />
                ),
              );

              const assertionNode =
                taskAssertions.length > 0 ? (
                  <AssertionPanel assertions={taskAssertions} taskProgress={taskProgress} />
                ) : (
                  <p className="maaspal-empty" style={{ padding: '0.5rem 0' }}>
                    {isActive ? 'Waiting for assertion data…' : 'No assertions defined for this step.'}
                  </p>
                );

              // Always put traffic first; side-by-side whenever traffic exists alongside assertions
              return taskTraffic.length > 0 && taskAssertions.length > 0 ? (
                <div className="maaspal-task-split">
                  <div className="maaspal-task-split__main">{trafficNodes}</div>
                  <div className="maaspal-task-split__aside">{assertionNode}</div>
                </div>
              ) : (
                <>
                  {trafficNodes}
                  {assertionNode}
                </>
              );
            })()}
          </>
        )}

        <div style={{ marginTop: '1rem' }}>
          <LogsPanel runId={runId} failed={run?.status.toUpperCase() === 'FAIL'} />
        </div>
      </PageSection>

      {showSettings && (
        <Suspense fallback={<Spinner size="lg" aria-label="Loading editor" />}>
          <RunSettingsModal runId={runId} onClose={() => setShowSettings(false)} />
        </Suspense>
      )}
    </>
  );
}
