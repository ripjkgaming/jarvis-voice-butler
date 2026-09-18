'use client';

import { useEffect, useRef, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import { useRoomContext } from '@livekit/components-react';

export type HudTaskEvent = {
  kind: 'tool_start' | 'tool_finish' | 'intent_echo' | 'log';
  tool?: string;
  label?: string;
  ok?: boolean;
  detail?: string;
  echo?: string;
  ts: number;
};

export type HudTask = HudTaskEvent & {
  id: string;
  startedAt: number;
  elapsedMs: number;
  done: boolean;
};

const MAX_LIVE = 5;

function parsePayload(raw: Uint8Array | string): HudTaskEvent | null {
  try {
    const text = typeof raw === 'string' ? raw : new TextDecoder().decode(raw);
    const obj = JSON.parse(text);
    if (!obj || typeof obj.kind !== 'string') return null;
    if (!['tool_start', 'tool_finish', 'intent_echo', 'log'].includes(obj.kind)) return null;
    return { ...obj, ts: obj.ts ?? Date.now() };
  } catch {
    return null;
  }
}

/**
 * Subscribes to the agent's data-channel task events.
 * Falls back to silence (consumers mine actions.log instead).
 */
export function useHudEvents() {
  const room = useRoomContext();
  const [events, setEvents] = useState<HudTaskEvent[]>([]);
  const [intentEcho, setIntentEcho] = useState<string>('');
  const [lastTool, setLastTool] = useState<string>('');
  const seq = useRef(0);

  useEffect(() => {
    if (!room) return;
    const onData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic && topic !== 'jarvis-tasks' && topic !== 'jarvis') return;
      const ev = parsePayload(payload);
      if (!ev) return;
      if (ev.kind === 'intent_echo' && ev.echo) setIntentEcho(ev.echo);
      if (ev.kind === 'tool_start' && ev.tool) setLastTool(ev.tool);
      setEvents((prev) => [...prev.slice(-60), ev]);
    };
    room.on(RoomEvent.DataReceived, onData);
    return () => {
      room.off(RoomEvent.DataReceived, onData);
    };
  }, [room]);

  const live: HudTask[] = (() => {
    const byTool = new Map<string, HudTask>();
    for (const ev of events) {
      if (ev.kind === 'tool_start' && ev.tool) {
        seq.current += 1;
        byTool.set(ev.tool, {
          ...ev,
          id: `${ev.tool}-${seq.current}`,
          startedAt: ev.ts,
          elapsedMs: Date.now() - ev.ts,
          done: false,
        });
      } else if (ev.kind === 'tool_finish' && ev.tool) {
        const t = byTool.get(ev.tool);
        if (t) {
          t.done = true;
          t.ok = ev.ok;
          t.elapsedMs = ev.ts - t.startedAt;
          t.detail = ev.detail ?? t.detail;
        }
      }
    }
    const active = [...byTool.values()].filter((t) => !t.done).slice(-MAX_LIVE);
    // refresh elapsed timers once per render tick
    return active.map((t) => ({ ...t, elapsedMs: Date.now() - t.startedAt }));
  })();

  return { events, live, intentEcho, lastTool };
}
