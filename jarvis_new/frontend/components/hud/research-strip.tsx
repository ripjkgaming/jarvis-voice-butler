'use client';

import { type BridgeSys } from '@/lib/bridge';

/** Live research jobs from bridge /sys: one slim amber bar each, under the
 *  ticker. Renders nothing when no research is running. */
export function ResearchStrip({
  sys,
  compact = false,
}: {
  sys: BridgeSys | null;
  compact?: boolean;
}) {
  const jobs = sys?.research ?? [];
  if (jobs.length === 0) return null;
  return (
    <div
      className={compact ? 'hud-research hud-research--compact' : 'hud-research'}
      aria-live="polite"
    >
      {jobs.map((j) => {
        const pct = Math.max(0, Math.min(100, Math.round(j.progress)));
        return (
          <div
            key={j.id}
            className="hud-research__job"
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Researching ${j.title}: ${pct}%, ${j.stage}`}
          >
            <span className="hud-research__tag">RESEARCH</span>
            <span className="hud-research__title">{j.title}</span>
            <span className="hud-research__track" aria-hidden="true">
              <span className="hud-research__fill" style={{ width: `${pct}%` }} />
            </span>
            <span className="hud-research__pct">{pct}%</span>
            <span className="hud-research__stage">{j.stage}</span>
          </div>
        );
      })}
    </div>
  );
}
