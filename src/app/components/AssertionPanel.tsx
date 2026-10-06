import { useState } from 'react';
import type { AssertionState, TaskProgressEntry } from '../api/client';
import { formatTaskName } from '../scenarioTitles';
import { statusStyle } from '../status';
import { COLOR, toneBg, toneColor } from '../styles/colors';

interface Props {
  assertions: AssertionState[];
  taskProgress: TaskProgressEntry[];
}


function formatValue(v: number | null): string {
  if (v === null) return '—';
  if (Number.isInteger(v) || Math.abs(v) >= 100) return Math.round(v).toLocaleString();
  return v.toFixed(2);
}

function withUnit(text: string, unit: string | null | undefined): string {
  if (!unit) return text;
  return unit === '%' || unit === 'ms' ? `${text}${unit === '%' ? '%' : ' ms'}` : `${text} ${unit}`;
}

function formatName(name: string): string {
  return name
    .replace(/_pct$/i, '_percent')
    .replace(/_ms$/i, ' (ms)')
    .replace(/_rps$/i, ' (rps)')
    .replace(/_percent$/i, ' (%)')
    .replace(/_/g, ' ')
    .replace(/\bp(\d+)\b/gi, 'P$1')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}


function AssertionCard({ a, muted }: { a: AssertionState; muted: boolean }) {
  const { tone, icon } = statusStyle(a.status);
  const color = toneColor(tone);
  const bg = toneBg(tone);
  const [showDetails, setShowDetails] = useState(false);
  return (
    <div
      className="maaspal-assertion-card"
      style={
        {
          '--assertion-color': color,
          '--assertion-bg': bg,
          opacity: muted ? 0.7 : 1,
          pointerEvents: muted ? 'none' : undefined,
        } as React.CSSProperties
      }
    >
      <span className="maaspal-assertion-card__name">{a.label || formatName(a.name)}</span>
      <span className="maaspal-assertion-card__value">
        {a.value === null && a.status === 'PENDING' ? 'waiting for data…' : withUnit(formatValue(a.value), a.unit)}
        {a.expected_value != null && ` vs. expected ${formatValue(a.expected_value)}`}
      </span>
      {a.target && (
        <span className="maaspal-assertion-card__target">expected: {withUnit(a.target, a.unit)}</span>
      )}
      {a.description && <span className="maaspal-assertion-card__desc">{a.description}</span>}
      {a.expression && (
        <>
          <button
            type="button"
            className="maaspal-assertion-card__details-toggle"
            onClick={() => setShowDetails((v) => !v)}
            aria-expanded={showDetails}
          >
            {showDetails ? 'Hide details' : 'Details'}
          </button>
          {showDetails && <span className="maaspal-assertion-card__expr">{a.expression}</span>}
        </>
      )}
      <span className="maaspal-assertion-card__status">
        <span>{icon}</span>
        <span>{a.status}</span>
      </span>
    </div>
  );
}

function AssertionGroup({
  label,
  taskStatus,
  assertions,
  muted,
}: {
  label: string;
  taskStatus: TaskProgressEntry['status'] | null;
  assertions: AssertionState[];
  muted: boolean;
}) {
  const borderColor = taskStatus ? toneColor(statusStyle(taskStatus).tone) : COLOR.borderSubtle;
  const statusLabel = taskStatus
    ? taskStatus.charAt(0) + taskStatus.slice(1).toLowerCase()
    : null;

  return (
    <div className="maaspal-assertion-group" style={{ opacity: muted ? 0.72 : 1 }}>
      <div
        className="maaspal-assertion-group__header"
        style={{ borderBottomColor: borderColor }}
      >
        <span className="maaspal-assertion-group__title">{label}</span>
        {statusLabel && (
          <span
            className="maaspal-assertion-group__status"
            style={{ color: borderColor }}
          >
            {statusLabel}
          </span>
        )}
      </div>
      <div className="maaspal-assertion-card-grid">
        {assertions.map((a) => (
          <AssertionCard key={a.name} a={a} muted={false} />
        ))}
      </div>
    </div>
  );
}

export function AssertionPanel({ assertions, taskProgress }: Props) {
  if (assertions.length === 0) {
    return (
      <div>
        <p className="maaspal-section-heading">Assertions</p>
        <p className="maaspal-empty" style={{ fontSize: '0.85rem' }}>
          Waiting for assertion data…
        </p>
      </div>
    );
  }

  // Build lookup from task name → TaskProgressEntry for status labels
  const progressByName = new Map(taskProgress.map((t) => [t.name, t]));

  // Group assertions by task name (null = global)
  const byTask = new Map<string | null, AssertionState[]>();
  for (const a of assertions) {
    const key = a.task ?? null;
    if (!byTask.has(key)) byTask.set(key, []);
    byTask.get(key)!.push(a);
  }

  // Order: task groups in the order tasks appear in taskProgress, then global
  const taskGroups: Array<{ key: string; entries: AssertionState[]; progress: TaskProgressEntry | undefined }> = [];
  for (const tp of taskProgress) {
    if (byTask.has(tp.name)) {
      taskGroups.push({ key: tp.name, entries: byTask.get(tp.name)!, progress: tp });
    }
  }
  // Tasks not in taskProgress (shouldn't happen, but guard)
  for (const [key, entries] of byTask) {
    if (key !== null && !progressByName.has(key)) {
      taskGroups.push({ key, entries, progress: undefined });
    }
  }
  const globalEntries = byTask.get(null) ?? [];

  const runningIdx = taskProgress.findIndex((t) => t.status === 'RUNNING');

  return (
    <div>
      <p className="maaspal-section-heading">Assertions</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        {taskGroups.map(({ key, entries, progress }, idx) => {
          const isRunning = progress?.status === 'RUNNING';
          const isDone =
            progress?.status === 'DONE' ||
            progress?.status === 'FAIL' ||
            progress?.status === 'CANCELLED';
          // Mute completed tasks when there is still a running task after them
          const muted = isDone && runningIdx > idx;
          return (
            <AssertionGroup
              key={key}
              label={formatTaskName(key)}
              taskStatus={progress?.status ?? null}
              assertions={entries}
              muted={muted || (isDone && !isRunning)}
            />
          );
        })}
        {globalEntries.length > 0 && (
          <AssertionGroup
            key="__global__"
            label="Global"
            taskStatus={null}
            assertions={globalEntries}
            muted={false}
          />
        )}
      </div>
    </div>
  );
}
