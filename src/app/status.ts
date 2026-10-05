import type { Tone } from './styles/colors';

// One place that decides how every status in the app looks: its tone (colour,
// via tokens.css), icon and wording. Run badges, task chips, the verdict,
// check cards and cleanup labels all read from here.

export interface StatusStyle {
  tone: Tone;
  icon: string;
}

const PENDING: StatusStyle = { tone: 'neutral', icon: '○' };
const RUNNING: StatusStyle = { tone: 'info', icon: '◎' };
const SUCCESS: StatusStyle = { tone: 'success', icon: '✓' };
const FAILURE: StatusStyle = { tone: 'danger', icon: '✗' };
const STOPPED: StatusStyle = { tone: 'warning', icon: '⊘' };

/** A run (PASS/FAIL/CANCELLED/RUNNING/PENDING) or task (DONE/FAIL/…) status. */
export function statusStyle(status: string | undefined): StatusStyle {
  switch ((status ?? '').toUpperCase()) {
    case 'PASS':
    case 'DONE':
    case 'PASSING':
      return SUCCESS;
    case 'FAIL':
    case 'FAILING':
      return FAILURE;
    case 'CANCELLED':
      return STOPPED;
    case 'RUNNING':
      return RUNNING;
    default:
      return PENDING;
  }
}

/** PatternFly Label colour for a run status (run history, run page header). */
export function statusLabelColor(status: string): 'green' | 'red' | 'orange' | 'blue' | 'grey' {
  const tone = statusStyle(status).tone;
  return ({ success: 'green', danger: 'red', warning: 'orange', info: 'blue', neutral: 'grey' } as const)[tone];
}

/** What happened to an object a run created. */
export const RESOURCE_STATUS: Record<string, { label: string; tone: Tone }> = {
  active: { label: 'exists', tone: 'neutral' },
  removed: { label: 'removed ✓', tone: 'success' },
  restored: { label: 'restored ✓', tone: 'success' },
  revoked: { label: 'revoked ✓', tone: 'success' },
  'cleanup failed': { label: 'cleanup failed ✗', tone: 'danger' },
  'left in place': { label: 'left in place', tone: 'warning' },
};
