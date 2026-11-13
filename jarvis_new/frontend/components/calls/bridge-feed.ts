'use client';

/** Live call events from the bridge (docs/calls/FRONTEND_WIRING.md).
 *
 *  The agent appends call events to `$JARVIS_CALLS_DIR/events.jsonl`; the
 *  bridge serves them as `GET /calls/state` (snapshot) and
 *  `GET /calls/events?since=<byte offset>`. This hook seeds from the
 *  snapshot, then polls with the byte cursor: every 500 ms while a call is
 *  live, every 2 s otherwise. Auth, base URL and the Tauri token come from
 *  lib/bridge.ts, so this works in the shell and in a plain browser. */
import { useEffect, useRef } from 'react';
import { bridgeGet, bridgePost } from '@/lib/bridge';

type Raw = Record<string, unknown>;

export type BridgeCallState = {
  ok?: boolean;
  active: Raw | null;
  transcript: Raw[];
  escalation: Raw | null;
  last_summary?: Raw | null;
  next: number;
};
export type BridgeCallEvents = { ok?: boolean; events: Raw[]; next: number };

export const bridgeCallSnapshot = () => bridgeGet<BridgeCallState>('/calls/state', 4000);
export const bridgeCallEvents = (since?: number) =>
  bridgeGet<BridgeCallEvents>(
    `/calls/events${since === undefined ? '' : `?since=${Math.max(0, Math.floor(since))}`}`,
    4000
  );
/** Owner command (takeover, hold, resume, hangup, accept, decline,
 *  take_message, stop_all). The HUD itself is voice-only and does not call
 *  this; it is here for a future button or the phone app. */
export const bridgeCallCommand = (command: string) =>
  bridgePost<{ ok: boolean; detail: string; id?: string }>('/calls/command', { command });

export const LIVE_POLL_MS = 500;
export const IDLE_POLL_MS = 2000;
/** Speaking levels arrive in poll-sized batches; replay them at this rate
 *  so the visualiser moves like the audio instead of jumping twice a second. */
const LEVEL_STEP_MS = 70;
const LEVEL_QUEUE_MAX = 16;

/** A call whose clock is this far from ours runs on another clock (the
 *  simulator pins a scenario's `now`). Its times are shifted onto ours so
 *  the timers and `+mm:ss` stamps stay right. Real calls are never shifted. */
const FOREIGN_CLOCK_MS = 15 * 60_000;

const ms = (t: unknown) =>
  typeof t === 'number' && Number.isFinite(t) && t > 0 ? (t < 1e12 ? t * 1000 : t) : null;

/** Shift event times by a per-call offset. The offset is learned from the
 *  newest time in the first batch that mentions the call (the latest
 *  speech, or the ring), which is close to "now" for a live call even when
 *  the HUD joins late through the snapshot. */
export function makeRetimer(now: () => number = Date.now) {
  const skew = new Map<string, number>();
  const learn = (events: Raw[]) => {
    const newest = new Map<string, number>();
    for (const ev of events) {
      const id = String(ev?.call_id ?? '');
      if (skew.has(id)) continue;
      const t = ms(ev?.event === 'state' ? ev.started_at : ev?.ts);
      if (t !== null) newest.set(id, Math.max(newest.get(id) ?? 0, t));
    }
    for (const [id, t] of newest) {
      const d = now() - t;
      skew.set(id, Math.abs(d) > FOREIGN_CLOCK_MS ? d : 0);
    }
  };
  const shift = (ev: Raw): Raw => {
    const d = skew.get(String(ev?.call_id ?? '')) ?? 0;
    if (!d) return ev;
    const out = { ...ev };
    const a = ms(ev.started_at);
    const b = ms(ev.ts);
    if (a !== null) out.started_at = a + d;
    if (b !== null) out.ts = b + d;
    return out;
  };
  return (events: Raw[]): Raw[] => {
    learn(events);
    return events.map(shift);
  };
}

/** Order snapshot pieces into the event sequence the reducer expects. */
export function snapshotEvents(s: BridgeCallState | null): Raw[] {
  if (!s || !s.active) return [];
  const out: Raw[] = [s.active, ...(Array.isArray(s.transcript) ? s.transcript : [])];
  if (s.escalation) out.push(s.escalation);
  return out;
}

/** Poll the bridge and hand each call event to `onEvent`. `live` tells the
 *  poller whether a call is up (fast cadence). Disabled when `enabled` is
 *  false. Never throws; a bridge outage just retries at the idle cadence. */
export function useBridgeCallSource(
  onEvent: (payload: unknown) => void,
  live: boolean,
  enabled = true
) {
  const liveRef = useRef(live);
  liveRef.current = live;

  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    let cursor: number | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const levels: Raw[] = [];
    const retime = makeRetimer();
    const drain = setInterval(() => {
      const ev = levels.shift();
      if (ev) onEvent(ev);
    }, LEVEL_STEP_MS);

    const feed = (events: Raw[]) => {
      for (const ev of retime(events)) {
        if (ev?.event === 'speaking') {
          levels.push(ev);
          if (levels.length > LEVEL_QUEUE_MAX) levels.splice(0, levels.length - LEVEL_QUEUE_MAX);
          continue;
        }
        // A state change (hold, takeover, end) silences any queued speech.
        if (ev?.event === 'state') levels.length = 0;
        onEvent(ev);
      }
    };

    const tick = async () => {
      if (stop) return;
      if (cursor === null) {
        const snap = await bridgeCallSnapshot();
        if (stop) return;
        if (snap && typeof snap.next === 'number') {
          feed(snapshotEvents(snap));
          cursor = snap.next;
        }
      } else {
        const res = await bridgeCallEvents(cursor);
        if (stop) return;
        if (res && typeof res.next === 'number') {
          // The bridge restarts from 0 after a truncation; follow its cursor.
          cursor = res.next;
          if (Array.isArray(res.events)) feed(res.events);
        }
      }
      timer = setTimeout(tick, liveRef.current ? LIVE_POLL_MS : IDLE_POLL_MS);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
      clearInterval(drain);
    };
  }, [enabled, onEvent]);
}
