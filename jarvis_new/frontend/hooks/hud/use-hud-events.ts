'use client';

import { useRef, useState } from 'react';
import { useDataTopic } from '@/lib/voice-session-react';

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

/** Topic payloads arrive already parsed from the voice socket. */
function parsePayload(raw: unknown): HudTaskEvent | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as { kind?: unknown; ts?: unknown };
  if (typeof obj.kind !== 'string') return null;
  if (!['tool_start', 'tool_finish', 'intent_echo', 'log'].includes(obj.kind)) return null;
  return { ...(raw as HudTaskEvent), ts: typeof obj.ts === 'number' ? obj.ts : Date.now() };
}

/**
 * Subscribes to the agent's data-channel task events.
 * Falls back to silence (consumers mine actions.log instead).
 */
export function useHudEvents() {
  const [events, setEvents] = useState<HudTaskEvent[]>([]);
  const [intentEcho, setIntentEcho] = useState<string>('');
  const [lastTool, setLastTool] = useState<string>('');
  const seq = useRef(0);

  const onEvent = (payload: unknown) => {
    const ev = parsePayload(payload);
    if (!ev) return;
    if (ev.kind === 'intent_echo' && ev.echo) setIntentEcho(ev.echo);
    if (ev.kind === 'tool_start' && ev.tool) setLastTool(ev.tool);
    setEvents((prev) => [...prev.slice(-60), ev]);
  };
  useDataTopic('jarvis-tasks', onEvent);
  useDataTopic('jarvis', onEvent);

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
