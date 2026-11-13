'use client';

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { CallHud } from './call-hud';
import css from './calls.module.css';
import {
  type Cue,
  DEMO_CONTACTS,
  DEMO_NEXT,
  DEMO_RULES,
  FREEZE,
  INBOUND,
  OUTBOUND,
  T0,
} from './mock-script';
import { useBridgeCallFeed, useCallEvents } from './use-call-events';

const LOOP_MS = 60_000;

/** Speech-like level: syllable bursts under a slow phrase envelope. */
function speechLevel(t: number) {
  const syll = Math.max(0, Math.sin(t * 13.7) * 0.6 + Math.sin(t * 5.3) * 0.4);
  const phrase = 0.55 + 0.45 * Math.sin(t * 1.3);
  return Math.min(1, 0.12 + syll * phrase * 0.95);
}

/** Demo driver for the Calls HUD (app/calls-demo). Plays the scripted
 *  inbound call on a loop, or freezes one state with `?state=`.
 *  `?source=bridge` shows real calls from the bridge feed instead. */
export function CallsDemo() {
  const params = useSearchParams();
  return params.get('source') === 'bridge' ? <BridgeDemo /> : <ScriptDemo />;
}

/** Live: whatever the backend is emitting (a real call or calls_sim.py --hud). */
function BridgeDemo() {
  const { call, level } = useBridgeCallFeed();
  if (!call) {
    return (
      <div className={css.standby}>
        <span>J.A.R.V.I.S. // COMMS</span>
        <b>STANDBY · NO CALL ON THE LINE</b>
        <em>BRIDGE FEED · /calls/events · POLL 2 S</em>
      </div>
    );
  }
  return <CallHud call={call} level={level} variant="page" />;
}

function ScriptDemo() {
  const params = useSearchParams();
  const frozen = FREEZE[params.get('state') ?? ''] ?? null;
  const { call, level, onEvent, clear } = useCallEvents();
  const [now, setNow] = useState(T0);
  const talk = useRef(false);

  // scripted events
  useEffect(() => {
    clear();
    talk.current = false;
    const script: Cue[] = frozen?.script === 'out' ? OUTBOUND : INBOUND;
    const apply = (c: Cue) => {
      if (c.ev) onEvent(c.ev, T0 + c.at);
      if (c.talk !== undefined) talk.current = c.talk;
    };
    if (frozen) {
      script.filter((c) => c.at <= frozen.at).forEach(apply);
      talk.current = frozen.talk;
      setNow(T0 + frozen.at);
      return;
    }
    let start = performance.now();
    let i = 0;
    const t = setInterval(() => {
      let el = performance.now() - start;
      if (el > LOOP_MS) {
        clear();
        start = performance.now();
        i = 0;
        el = 0;
      }
      while (i < script.length && script[i].at <= el) apply(script[i++]);
      setNow(T0 + el);
    }, 100);
    return () => clearInterval(t);
  }, [frozen, onEvent, clear]);

  // synthetic Jarvis output level (the agent's `speaking` events)
  useEffect(() => {
    const t0 = performance.now();
    const t = setInterval(() => {
      const s = (performance.now() - t0) / 1000;
      onEvent({
        type: 'call',
        event: 'speaking',
        call_id: 'demo',
        level: talk.current ? speechLevel(s) : 0,
      });
    }, 50);
    return () => clearInterval(t);
  }, [onEvent]);

  if (!call) return <div style={{ position: 'fixed', inset: 0, background: '#010509' }} />;
  return (
    <CallHud
      call={call}
      level={level}
      now={now}
      rules={DEMO_RULES}
      nextCall={DEMO_NEXT}
      contacts={DEMO_CONTACTS}
      variant="page"
      disposition={
        call.direction === 'in'
          ? [
              'PROTOCOL ▸ JARVIS SCREENS',
              'RULE 02 · ANSWER ALL · DEEP WORK',
              'AI DISCLOSED AT PICKUP · TRANSCRIBED',
              'YOU CAN TAKE IT ANY TIME',
            ]
          : undefined
      }
    />
  );
}
