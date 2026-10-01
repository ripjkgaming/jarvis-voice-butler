'use client';

import { type ReactNode, useEffect, useMemo, useState } from 'react';
import { useBootLog } from '@/hooks/hud/use-boot-log';
import { type JarvisState } from '@/hooks/hud/use-jarvis-state';
import { useCaptions, useLiveCaption, useRoom } from '@/hooks/hud/use-room-state';
import { type BridgeSys } from '@/lib/bridge';
import { useSaid } from '@/lib/hud-say';
import { type VoiceLink } from '@/lib/voice-link';
import s from './stark.module.css';

const STATE_WORD: Record<JarvisState, string> = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'PROCESSING',
  speaking: 'RESPONDING',
};

/** The big state word under the reactor plus the spoken-command hint. */
export function StateReadout({
  jarvis,
  muted,
  link,
}: {
  jarvis: JarvisState;
  muted: boolean | null;
  link: VoiceLink;
}) {
  const word = muted === true ? 'MUTED' : link.phase !== 'idle' ? link.word : STATE_WORD[jarvis];
  let hint: ReactNode;
  if (muted === true) hint = <>MICROPHONE OFF · PRESS M OR USE THE TRAY TO RESTORE</>;
  else if (link.phase !== 'idle') hint = <>{link.detail}</>;
  else if (jarvis === 'idle')
    hint = (
      <>
        SAY <b>“JARVIS”</b> · OR PRESS NUMPAD ENTER
      </>
    );
  else if (jarvis === 'listening') hint = <>GO AHEAD, SIR</>;
  else if (jarvis === 'thinking') hint = <>WORKING ON IT</>;
  else hint = <>SAY “STOP” TO INTERRUPT</>;
  return (
    <div className={s.readout} role="status" aria-live="polite">
      <span className={s.readoutWord}>{word}</span>
      <span className={s.readoutHint}>{hint}</span>
    </div>
  );
}

const LIVE_WORDS = 48;

/** Subtitle line: Jarvis's word-synced live line while he speaks, the
 *  newest caption during a call, typed /chat echoes, else nothing. */
export function Caption() {
  const said = useSaid();
  const inCall = useRoom() !== null;
  const captions = useCaptions(inCall);
  const [speaking, setSpeaking] = useState(false);
  const liveRaw = useLiveCaption(inCall, speaking ? 100 : 300);
  useEffect(() => setSpeaking(!!liveRaw && !liveRaw.done), [liveRaw]);
  const live = inCall ? liveRaw : null;
  const last = useMemo(() => {
    const l = captions.at(-1);
    return inCall && l && Date.now() / 1000 - l.ts < 120 ? l : null;
  }, [captions, inCall]);
  const showLive = !said && live && live.text && (!live.done || live.ts >= (last?.ts ?? 0));

  let who: string | null = null;
  let body: ReactNode = null;
  if (said) {
    who = 'ECHO';
    body = said;
  } else if (showLive) {
    who = 'JARVIS';
    const words = live.text.split(' ');
    const start = Math.max(0, words.length - LIVE_WORDS);
    body = words.slice(start).map((w, i) => (
      <span key={start + i} className={s.captionWord}>
        {w}{' '}
      </span>
    ));
  } else if (last) {
    who = last.role === 'sir' ? 'SIR' : 'JARVIS';
    body = last.text;
  }
  return (
    <div className={s.caption} data-who={who ?? 'none'} aria-live="polite">
      {who ? (
        <>
          <span className={s.captionWho}>{who}</span>
          <p className={s.captionText}>
            <span>{body}</span>
          </p>
        </>
      ) : (
        <p className={s.captionIdle}>VOICE CHANNEL CLEAR</p>
      )}
    </div>
  );
}

/** Call set-up readout while "Hey Jarvis" comes up: real steps, timed. */
export function BootSequence() {
  const view = useBootLog();
  if (!view) return null;
  return (
    <div className={s.boot} data-online={view.online} role="status" aria-live="polite">
      <div className={s.bootHead}>
        <span>LINK</span>
        <span>{view.online ? 'ESTABLISHED' : `STEP ${String(view.step).padStart(2, '0')}`}</span>
      </div>
      <ol className={s.bootRows}>
        {view.rows.slice(-5).map((r) => (
          <li key={r.key} className={s.bootRow} data-done={r.done}>
            <span className={s.bootLabel}>{r.label}</span>
            <span className={s.bootDetail}>{r.detail}</span>
            <span className={s.bootAt}>{r.at}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Running research jobs with progress (bridge /sys research). */
export function Research({ sys }: { sys: BridgeSys | null }) {
  const jobs = sys?.research ?? [];
  if (jobs.length === 0) return null;
  return (
    <ul className={s.research} aria-label="Research in progress">
      {jobs.slice(0, 2).map((j) => {
        const pct = Math.max(0, Math.min(100, Math.round(j.progress)));
        return (
          <li key={j.id} className={s.researchJob}>
            <span className={s.researchTag}>RESEARCH</span>
            <span className={s.researchTitle}>{j.title}</span>
            <span className={s.researchStage}>{j.stage}</span>
            <span className={s.researchPct}>{pct}%</span>
            <span className={s.researchBar} aria-hidden="true">
              <i style={{ width: `${pct}%` }} />
            </span>
          </li>
        );
      })}
    </ul>
  );
}
