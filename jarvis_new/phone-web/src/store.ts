/** App state (PLAN.md §5): Preact signals plus pure reducers.
 * One writer per slice: poll.ts → laptop/voice/link, call.ts → call/captions,
 * api.ts → auth (on auth errors). Real data only: unknown is null, shown "--". */
import { computed, signal } from '@preact/signals';

export type ControlAction =
  | 'guest_on'
  | 'guest_off'
  | 'lock'
  | 'unlock'
  | 'screens_off'
  | 'screens_on'
  | 'sleep'
  | 'restart'
  | 'shutdown'
  | 'mic_mute'
  | 'mic_unmute';
export const ACTIONS: ControlAction[] = [
  'guest_on',
  'guest_off',
  'lock',
  'unlock',
  'screens_off',
  'screens_on',
  'sleep',
  'restart',
  'shutdown',
  'mic_mute',
  'mic_unmute',
];
export const STEPUP: ReadonlySet<ControlAction> = new Set(['guest_off', 'unlock', 'restart', 'shutdown']);

export type Lamp<T> = { value: T | null; at: number };
export type Page = 'gate' | 'core' | 'deck';
export type Agent = 'idle' | 'listening' | 'thinking' | 'speaking';
export type CallPhase = 'idle' | 'minting' | 'connecting' | 'live' | 'reconnecting' | 'ending' | 'error';

export type Link = { phase: 'online' | 'offline' | 'connecting'; rttMs: number | null; lastOk: number | null };
export type Auth = {
  paired: boolean;
  passkey: boolean;
  gate: 'cold' | 'soft' | 'open';
  gateExpiresAt: number | null;
  softAt: number | null;
  lockoutUntil: number | null;
  triesLeft: number | null;
};
export type Power = { percent: number; charging: boolean };
export type Laptop = {
  host: string | null;
  guest: Lamp<boolean>;
  locked: Lamp<boolean>;
  screens: Lamp<'on' | 'off'>;
  micMuted: Lamp<boolean>;
  presence: Lamp<'present' | 'absent' | 'unknown'>;
  power: Lamp<Power>;
};
export type Voice = { callLive: boolean; owner: string | null; agentState: string | null; waking: boolean };
export type Call = {
  phase: CallPhase;
  muted: boolean;
  room: string | null;
  attempt: number;
  error: string | null;
  agent: Agent;
  micRms: number;
  playRms: number;
};
export type Caption = { id: string; who: 'user' | 'jarvis' | 'system'; text: string; final: boolean; viaRoute?: number; decode?: boolean };
export type ActionState = { phase: 'idle' | 'pending' | 'ok' | 'warn' | 'failed'; error?: string; at: number };

const none = <T>(): Lamp<T> => ({ value: null, at: 0 });
export const emptyLaptop = (): Laptop => ({
  host: null,
  guest: none(),
  locked: none(),
  screens: none(),
  micMuted: none(),
  presence: none(),
  power: none(),
});

export const link = signal<Link>({ phase: 'connecting', rttMs: null, lastOk: null });
export const auth = signal<Auth>({
  paired: false,
  passkey: false,
  gate: 'cold',
  gateExpiresAt: null,
  softAt: null,
  lockoutUntil: null,
  triesLeft: null,
});
export const laptop = signal<Laptop>(emptyLaptop());
export const voice = signal<Voice>({ callLive: false, owner: null, agentState: null, waking: false });
export const call = signal<Call>({
  phase: 'idle',
  muted: false,
  room: null,
  attempt: 0,
  error: null,
  agent: 'idle',
  micRms: 0,
  playRms: 0,
});
export const captions = signal<{ lines: Caption[] }>({ lines: [] });
export const page = signal<{ current: Page; target: Page }>({ current: 'gate', target: 'gate' });
export const actions = signal<Record<string, ActionState>>({});
export const sw = signal<{ updateReady: boolean }>({ updateReady: false });
export const toast = signal<{ text: string; tone: 'info' | 'fail'; at: number } | null>(null);
/** Tick that advances once a second so ages re-render (not state). */
export const clock = signal(Date.now());

// ---------------------------------------------------------------- reducers

type StateJson = {
  laptop?: {
    host?: string;
    guest?: boolean | null;
    locked?: boolean | null;
    screens?: 'on' | 'off' | null;
    mic_muted?: boolean | null;
    presence?: string | null;
    power?: { percent: number; charging: boolean } | null;
  };
  voice?: { call_live?: boolean; voice_owner?: string | null; agent_state?: string | null; waking?: boolean };
  gate?: { state?: string; expires_in_s?: number | null; soft_in_s?: number | null };
  ages?: Record<string, number | null>;
};

const lampOf = <T>(value: T | null | undefined, age: number | null | undefined, now: number): Lamp<T> =>
  value === null || value === undefined ? { value: null, at: 0 } : { value, at: now - (age ?? 0) * 1000 };

export function laptopFromState(json: StateJson, now: number): Laptop {
  const l = json.laptop ?? {};
  const ages = json.ages ?? {};
  const presence = l.presence === 'present' || l.presence === 'absent' ? l.presence : l.presence ? 'unknown' : null;
  const power =
    l.power && typeof l.power.percent === 'number' ? { percent: l.power.percent, charging: !!l.power.charging } : null;
  return {
    host: l.host ?? null,
    guest: lampOf(typeof l.guest === 'boolean' ? l.guest : null, 0, now),
    locked: lampOf(typeof l.locked === 'boolean' ? l.locked : null, ages.locked, now),
    screens: lampOf(l.screens === 'on' || l.screens === 'off' ? l.screens : null, ages.screens, now),
    micMuted: lampOf(typeof l.mic_muted === 'boolean' ? l.mic_muted : null, ages.mic_muted, now),
    presence: lampOf(presence as Laptop['presence']['value'], ages.presence, now),
    power: lampOf(power, 0, now),
  };
}

export function voiceFromState(json: StateJson): Voice {
  const v = json.voice ?? {};
  return {
    callLive: !!v.call_live,
    owner: v.voice_owner ?? null,
    agentState: v.agent_state ?? null,
    waking: !!v.waking,
  };
}

/** Merge a /control response's `state` (laptop object, no ages) as fresh readbacks. */
export function laptopFromControl(prev: Laptop, state: StateJson['laptop'], now: number): Laptop {
  if (!state) return prev;
  const next = laptopFromState({ laptop: state, ages: {} }, now);
  // A readback that came back null keeps the previous value rather than erasing it.
  const keep = <K extends keyof Laptop>(k: K) => ((next[k] as Lamp<unknown>).value === null ? prev[k] : next[k]);
  return {
    host: next.host ?? prev.host,
    guest: keep('guest') as Laptop['guest'],
    locked: keep('locked') as Laptop['locked'],
    screens: keep('screens') as Laptop['screens'],
    micMuted: keep('micMuted') as Laptop['micMuted'],
    presence: keep('presence') as Laptop['presence'],
    power: keep('power') as Laptop['power'],
  };
}

export const MAX_CAPTIONS = 50;
export function addCaption(lines: Caption[], line: Caption): Caption[] {
  const last = lines[lines.length - 1];
  let out: Caption[];
  if (last && !last.final && last.who === line.who) out = [...lines.slice(0, -1), { ...line, id: last.id }];
  else out = [...lines, line];
  return out.length > MAX_CAPTIONS ? out.slice(out.length - MAX_CAPTIONS) : out;
}

export type Glyph = 'star' | 'orbit' | 'flame' | 'play' | 'locked';
export function glyphFor(locked: boolean | null, agent: Agent | string | null): Glyph {
  if (locked === true) return 'locked';
  if (agent === 'listening') return 'orbit';
  if (agent === 'thinking') return 'flame';
  if (agent === 'speaking') return 'play';
  return 'star';
}

export function fmtAge(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export type Tone = 'on' | 'warn' | 'off' | 'guest' | 'unknown' | 'fail';
export type StripLamp = { key: string; label: string; value: string; tone: Tone; bolt?: boolean; stale?: string };

export function stripLamps(
  l: Link,
  lap: Laptop,
  c: Pick<Call, 'phase'>,
  v: Pick<Voice, 'callLive'>,
  updateReady: boolean,
  now: number,
): StripLamp[] {
  const offline = l.phase === 'offline';
  const stale = (lamp: Lamp<unknown>) => (offline && lamp.value !== null ? fmtAge(now - lamp.at) : undefined);
  const lamps: StripLamp[] = [];
  lamps.push({
    key: 'LNK',
    label: 'LNK',
    value: l.phase === 'online' && l.rttMs !== null ? `${Math.round(l.rttMs)}ms` : l.phase === 'offline' ? 'OFF' : '--',
    tone: l.phase === 'online' ? 'on' : l.phase === 'offline' ? 'fail' : 'unknown',
  });
  const callValue =
    c.phase === 'live'
      ? 'LIVE'
      : c.phase === 'reconnecting'
        ? 'RCN'
        : c.phase === 'minting' || c.phase === 'connecting'
          ? 'DIAL'
          : v.callLive
            ? 'LIVE'
            : 'IDLE';
  lamps.push({
    key: 'CALL',
    label: 'CALL',
    value: callValue,
    tone: c.phase === 'live' ? 'on' : callValue === 'IDLE' ? 'off' : 'warn',
  });
  const g = lap.guest.value;
  lamps.push({ key: 'GST', label: 'GST', value: g === null ? '--' : g ? 'ON' : 'OFF', tone: g === null ? 'unknown' : g ? 'guest' : 'off', stale: stale(lap.guest) });
  const k = lap.locked.value;
  lamps.push({ key: 'LCK', label: 'LCK', value: k === null ? '--' : k ? 'LOCK' : 'OPEN', tone: k === null ? 'unknown' : k ? 'warn' : 'on', stale: stale(lap.locked) });
  const p = lap.power.value;
  lamps.push({
    key: 'PWR',
    label: 'PWR',
    value: p === null ? '--' : `${p.percent}%`,
    bolt: !!p?.charging,
    tone: p === null ? 'unknown' : p.charging || p.percent > 20 ? 'on' : 'warn',
    stale: stale(lap.power),
  });
  const pr = lap.presence.value;
  lamps.push({
    key: 'PRS',
    label: 'PRS',
    value: pr === 'present' ? 'DESK' : pr === 'absent' ? 'AWAY' : '--',
    tone: pr === 'present' ? 'on' : pr === 'absent' ? 'off' : 'unknown',
    stale: stale(lap.presence),
  });
  if (updateReady) lamps.push({ key: 'UPD', label: 'UPD', value: 'NEW', tone: 'warn' });
  if (offline) for (const x of lamps) if (x.key !== 'LNK' && x.key !== 'UPD') x.tone = x.value === '--' ? 'unknown' : 'off';
  return lamps;
}

export const canControl = computed(() => link.value.phase === 'online' && auth.value.gate === 'open');
export const glyph = computed(() => glyphFor(laptop.value.locked.value, call.value.phase === 'live' ? call.value.agent : null));
export const guestOn = computed(() => laptop.value.guest.value === true);
export const lamps = computed(() =>
  stripLamps(link.value, laptop.value, call.value, voice.value, sw.value.updateReady, clock.value),
);

/** Local navigation words, matched before anything is sent (PLAN.md §4.3). */
export function navWord(text: string): 'deck' | 'core' | null {
  const t = text.trim().toLowerCase().replace(/[.!?]+$/, '');
  if (/^(open )?(the )?deck$/.test(t)) return 'deck';
  if (/^core$/.test(t)) return 'core';
  return null;
}

export function showToast(text: string, tone: 'info' | 'fail' = 'info') {
  toast.value = { text, tone, at: Date.now() };
}

// ------------------------------------------------------------- persistence

const KEY = 'link.v1';
type Persisted = { page?: Page; captions?: Caption[]; pk?: boolean };
export function loadPersisted(): Persisted {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Persisted) : {};
  } catch {
    return {};
  }
}
export function savePersisted(p: Persisted) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...loadPersisted(), ...p }));
  } catch {
    /* comfort only */
  }
}
