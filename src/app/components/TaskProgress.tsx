import { useEffect, useState } from 'react';
import type { TaskProgressEntry } from '../api/client';
import { formatTaskName } from '../scenarioTitles';
import { statusStyle } from '../status';
import { toneColor } from '../styles/colors';

interface Props {
  tasks: TaskProgressEntry[];
  selectedView: string;
  onSelect: (view: string) => void;
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

/** Re-renders once a second so a RUNNING task's elapsed time visibly ticks up
 * between the 2s progress polls, without the backend ever pushing a live number. */
function useTick(active: boolean): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
}

export function TaskProgress({ tasks, selectedView, onSelect }: Props) {
  useTick(tasks.some((t) => t.status === 'RUNNING' && !!t.started_at));

  if (tasks.length === 0) return null;

  return (
    <div className="maaspal-task-pipeline">
      <button
        type="button"
        className={`maaspal-overview-btn${selectedView === 'overview' ? ' maaspal-overview-btn--selected' : ''}`}
        onClick={() => onSelect('overview')}
        aria-pressed={selectedView === 'overview'}
      >
        <span className="maaspal-overview-btn__icon">≡</span>
        <span className="maaspal-overview-btn__label">Overview</span>
      </button>

      {tasks.map((task, i) => {
        const color = toneColor(statusStyle(task.status).tone);
        const isRunning = task.status === 'RUNNING';
        const isSelected = selectedView === task.name;
        const total = task.progress?.total ?? null;
        // A DONE task's bar is full only when its total was the plan (N of N
        // requests) — a "tokens toward the limit" bar shows where it really
        // ended up.
        const pct =
          task.progress && total && total > 0
            ? task.status === 'DONE' && !task.progress.unit
              ? 100
              : Math.min(100, Math.round((task.progress.current / total) * 100))
            : null;
        const unit = task.progress?.unit ? ` ${task.progress.unit}` : '';
        const progressLabel = task.progress
          ? total
            ? `${task.progress.current.toLocaleString()} / ${total.toLocaleString()}${unit}`
            : `${task.progress.current.toLocaleString()}${unit || ' sent'}`
          : null;
        const badgeColor = task.assertions_status
          ? toneColor(statusStyle(task.assertions_status).tone)
          : null;
        const durationLabel =
          typeof task.duration_ms === 'number'
            ? formatDuration(task.duration_ms)
            : isRunning && task.started_at
              ? formatDuration(Date.now() - new Date(task.started_at).getTime())
              : null;

        return (
          <div key={task.name} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            {i > 0 && <span className="maaspal-task-pipeline__arrow">→</span>}
            <button
              type="button"
              className={`maaspal-task-chip maaspal-task-chip--${task.status.toLowerCase()}${isSelected ? ' maaspal-task-chip--selected' : ''}`}
              style={{ '--task-color': color } as React.CSSProperties}
              title={task.summary}
              onClick={() => onSelect(task.name)}
              aria-pressed={isSelected}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                {isRunning ? (
                  <span className="maaspal-task-chip__spinner" />
                ) : (
                  <span className="maaspal-task-chip__icon">{statusStyle(task.status).icon}</span>
                )}
                <span className="maaspal-task-chip__name">{formatTaskName(task.name)}</span>
                {durationLabel && (
                  <span className="maaspal-task-chip__duration">{durationLabel}</span>
                )}
                {badgeColor && (
                  <span
                    className="maaspal-task-chip__assertion-badge"
                    style={{ background: badgeColor }}
                    title={`Assertions: ${task.assertions_status}`}
                  />
                )}
              </div>
              {progressLabel && (
                <div className="maaspal-task-chip__progress-wrap">
                  {pct !== null && (
                    <div className="maaspal-task-chip__progress-bar">
                      <div
                        className="maaspal-task-chip__progress-fill"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  )}
                  <span className="maaspal-task-chip__progress-label">{progressLabel}</span>
                </div>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
