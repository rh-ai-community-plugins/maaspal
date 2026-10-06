import { useEffect, useState } from 'react';
import { Tooltip } from '@patternfly/react-core';
import { LOGO_SRC, useIsDarkTheme } from '../logos';
import { bubbleText, STAGE_TITLE, type PalCondition, type PalState } from './palState';
import { usePal } from './usePal';
import './pal.css';

// The hatching animation, as a sequence of frames: the logo drops into the
// strip as an egg, wobbles, cracks (a key glints through), splits, and baby
// PAL pops out.
export type HatchPhase = 'drop' | 'wobble' | 'crack1' | 'crack2' | 'split' | 'done';
export const HATCH_FRAMES: [HatchPhase, number][] = [
  ['drop', 500],
  ['wobble', 900],
  ['crack1', 400],
  ['crack2', 500],
  ['split', 700],
];

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function useHatch(onDone: () => void): HatchPhase {
  const [phase, setPhase] = useState<HatchPhase>(() => (prefersReducedMotion() ? 'done' : 'drop'));
  useEffect(() => {
    if (phase === 'done') {
      onDone();
      return;
    }
    const i = HATCH_FRAMES.findIndex(([p]) => p === phase);
    const next = HATCH_FRAMES[i + 1]?.[0] ?? 'done';
    const id = window.setTimeout(() => setPhase(next), HATCH_FRAMES[i][1]);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);
  return phase;
}

const EGG_PATH = 'M20 2 C8 2 2 22 2 32 C2 43 10 48 20 48 C30 48 38 43 38 32 C38 22 32 2 20 2 Z';

function Egg({ phase }: { phase: HatchPhase }) {
  const cracked = phase === 'crack1' || phase === 'crack2' || phase === 'split';
  const body = (
    <>
      <path d={EGG_PATH} className="maaspal-pal__egg-shell" />
      <circle cx="13" cy="18" r="3" className="maaspal-pal__egg-spot" />
      <circle cx="26" cy="13" r="2" className="maaspal-pal__egg-spot" />
      <circle cx="28" cy="38" r="3.5" className="maaspal-pal__egg-spot" />
      {cracked && (
        <polyline
          points={phase === 'crack1' ? '8,28 13,24 17,30 22,25' : '2,30 8,26 12,31 17,25 22,30 27,24 32,29 38,27'}
          className="maaspal-pal__egg-crack"
        />
      )}
      {phase === 'crack2' && (
        // A tiny API key glinting through the crack.
        <g className="maaspal-pal__egg-key">
          <circle cx="20" cy="28" r="2" />
          <rect x="21.5" y="27.3" width="6" height="1.4" />
        </g>
      )}
    </>
  );
  if (phase !== 'split') {
    return (
      <svg viewBox="0 0 40 50" className={`maaspal-pal__egg maaspal-pal__egg--${phase}`} aria-hidden="true">
        {body}
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 40 50" className="maaspal-pal__egg maaspal-pal__egg--split" aria-hidden="true">
      <defs>
        <clipPath id="maaspal-pal-top">
          <polygon points="0,0 40,0 40,27 32,29 27,24 22,30 17,25 12,31 8,26 0,30" />
        </clipPath>
        <clipPath id="maaspal-pal-bottom">
          <polygon points="0,30 8,26 12,31 17,25 22,30 27,24 32,29 40,27 40,50 0,50" />
        </clipPath>
      </defs>
      <g clipPath="url(#maaspal-pal-top)" className="maaspal-pal__shell-top">
        {body}
      </g>
      <g clipPath="url(#maaspal-pal-bottom)" className="maaspal-pal__shell-bottom">
        {body}
      </g>
    </svg>
  );
}

function Hatching({ pal, onDone }: { pal: PalState; onDone: () => void }) {
  const phase = useHatch(onDone);
  return (
    <div className="maaspal-pal__hatch" data-phase={phase}>
      {phase === 'drop' ? (
        <img src={LOGO_SRC[pal.colourway]} alt="" className="maaspal-pal__drop" />
      ) : (
        <Egg phase={phase} />
      )}
      {phase === 'split' && <img src={LOGO_SRC[pal.colourway]} alt="" className="maaspal-pal__newborn" />}
    </div>
  );
}

function age(pal: PalState, now: number): string {
  const ms = Math.max(0, now - (pal.hatchedAt ?? now));
  const days = Math.floor(ms / 86_400_000);
  if (days > 0) return `${days} day${days === 1 ? '' : 's'}`;
  const hours = Math.floor(ms / 3_600_000);
  if (hours > 0) return `${hours} hour${hours === 1 ? '' : 's'}`;
  return `${Math.max(1, Math.floor(ms / 60_000))} min`;
}

function StatusTip({ pal, c, now }: { pal: PalState; c: PalCondition; now: number }) {
  return (
    <div className="maaspal-pal__tip">
      <strong>{c.expired ? 'PAL (expired)' : STAGE_TITLE[c.stage]}</strong>
      <div>Age: {age(pal, now)}</div>
      <div>Fullness: {Math.round(pal.fullness)}%</div>
      <div>Mood: {c.moodLabel}</div>
      <div>Tokens eaten: {pal.lifetimeTokens.toLocaleString()}</div>
      {pal.lastFedAt !== null && <div>Last meal: {pal.lastMealTokens.toLocaleString()} tokens</div>}
      {c.litter > 0 && <div>Keys left lying around: {c.litter} (Clean Up Now tidies them)</div>}
    </div>
  );
}

const MAX_LITTER = 8;
const WAKE_MS = 2 * 60_000;

/** PAL, walking along the top of the plugin. No buttons: it eats what your
 * runs send, sulks at failures, and its floor collects whatever cleanup left. */
export function PalPet() {
  const { pal, condition: c, now, hatched } = usePal();
  const dark = useIsDarkTheme();
  if (!pal || !c) return null;

  if (pal.hatchedAt === null) {
    return (
      <div className="maaspal-pal" role="img" aria-label="PAL is hatching">
        <Hatching pal={pal} onDone={hatched} />
      </div>
    );
  }

  // Dark theme is night: PAL dozes off, unless a run is going or just happened.
  const sleeping = dark && !c.busy && !(pal.lastEvent && now - pal.lastEvent.at < WAKE_MS);
  const bubble = bubbleText(pal, c, sleeping, now);
  const resting = sleeping || c.throttled || c.expired;
  const pose = c.expired ? 'ghost' : c.throttled ? 'panting' : sleeping ? 'sleeping' : c.moodLabel;
  const label = `PAL, ${STAGE_TITLE[c.stage]}: ${Math.round(pal.fullness)}% full, ${c.moodLabel}`;
  return (
    <div className="maaspal-pal" aria-label="PAL, the MaaS:PAL pet">
      {Array.from({ length: Math.min(c.litter, MAX_LITTER) }, (_, i) => (
        <span
          key={i}
          className="maaspal-pal__litter"
          style={{ left: `${6 + ((i * 37) % 88)}%`, rotate: `${(i % 2 ? 1 : -1) * (6 + i * 3)}deg` }}
          aria-hidden="true"
        >
          sk-oai-…
        </span>
      ))}
      <div
        className="maaspal-pal__walker"
        data-busy={c.busy && !resting ? 'true' : undefined}
        data-resting={resting ? 'true' : undefined}
      >
        <Tooltip content={<StatusTip pal={pal} c={c} now={now} />}>
          <div
            className="maaspal-pal__body"
            data-stage={c.stage}
            data-pose={pose}
            data-sick={c.sick ? 'true' : undefined}
            tabIndex={0}
            role="img"
            aria-label={label}
          >
            {bubble && <span className="maaspal-pal__bubble">{bubble}</span>}
            <img
              src={LOGO_SRC[pal.colourway]}
              alt=""
              className="maaspal-pal__sprite"
              style={{ scale: `${1 + pal.fullness / 400} 1` }}
            />
            {c.busy && !resting && <span className="maaspal-pal__prop" aria-hidden="true">🔑</span>}
            {c.sick && !c.expired && <span className="maaspal-pal__prop" aria-hidden="true">🌡️</span>}
            {c.stage === 'guardian' && !c.expired && (
              <span className="maaspal-pal__crown" aria-hidden="true">👑</span>
            )}
          </div>
        </Tooltip>
      </div>
    </div>
  );
}
