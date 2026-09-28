'use client';

import { useEffect, useState } from 'react';
import { JARVIS_COLORS, type JarvisState, MUTED_COLOR } from '@/hooks/hud/use-jarvis-state';
import { type BridgeSys, bridgeCaptions } from '@/lib/bridge';

const STATE_LABEL: Record<JarvisState, string> = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
};

/** A caption stays on the strip this long after it was said. */
const CAPTION_FRESH_S = 45;

type Caption = { who: 'Sir' | 'Jarvis'; text: string; ts: number };

/** Latest conversation line from the bridge captions tail. Fail-soft. */
function useLatestCaption(): Caption | null {
  const [caption, setCaption] = useState<Caption | null>(null);
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const lines = await bridgeCaptions(2);
      if (cancelled) return;
      const last = lines?.at(-1);
      setCaption(
        last && Date.now() / 1000 - last.ts < CAPTION_FRESH_S
          ? { who: last.role === 'sir' ? 'Sir' : 'Jarvis', text: last.text, ts: last.ts }
          : null
      );
    };
    void poll();
    const timer = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
  return caption;
}

/** School mode: the whole HUD collapses into one quiet strip docked above
 *  the taskbar. The window itself is click-through and never focusable
 *  (shell/src-tauri/src/school.rs), so this is display-only by design. */
export function SchoolStrip({
  sys,
  jarvis,
  muted,
}: {
  sys: BridgeSys | null;
  jarvis: JarvisState;
  muted: boolean | null;
}) {
  const caption = useLatestCaption();
  const job = sys?.research?.[0] ?? null;
  const pct = job ? Math.max(0, Math.min(100, Math.round(job.progress))) : 0;
  const active = jarvis !== 'idle' || caption !== null;
  const color = muted ? MUTED_COLOR : JARVIS_COLORS[jarvis];

  return (
    <div
      className={active ? 'school-strip school-strip--active' : 'school-strip'}
      style={{ ['--strip-accent' as string]: color }}
      role="status"
      aria-live="polite"
      aria-label={`Jarvis school mode, ${muted ? 'microphone muted' : STATE_LABEL[jarvis].toLowerCase()}`}
    >
      <div className="school-strip__row">
        <span className="school-strip__dot" aria-hidden="true" />
        <span className="school-strip__state">{muted ? 'MUTED' : STATE_LABEL[jarvis]}</span>
        <span className="school-strip__tag">SCHOOL</span>
        {job ? (
          <span className="school-strip__job" title={job.stage}>
            <span className="school-strip__track" aria-hidden="true">
              <span className="school-strip__fill" style={{ width: `${pct}%` }} />
            </span>
            <span className="school-strip__pct">{pct}%</span>
          </span>
        ) : null}
      </div>
      <div className="school-strip__caption">
        {caption ? (
          <>
            <b className={caption.who === 'Sir' ? 'is-sir' : 'is-jarvis'}>{caption.who}</b>
            <span>{caption.text}</span>
          </>
        ) : (
          <span className="school-strip__hint">Say “hey Jarvis” · “exit school mode” to leave</span>
        )}
      </div>
    </div>
  );
}
