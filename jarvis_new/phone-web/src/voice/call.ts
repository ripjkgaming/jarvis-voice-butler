/** Call lifecycle (PLAN.md §8, §9.2). Sole writer of the call and captions
 * slices. Close codes from docs/voice-transport.md drive the transitions;
 * network loss re-mints a fresh private room (rooms cannot be rejoined) with
 * 0.5/1/2/4/8 s backoff and gives up after 30 s. */
import { addCaption, call, captions, navWord, type Agent, type Call } from '../store';
import { parse, topic } from './ws';

export type SocketLike = {
  binaryType: string;
  readyState: number;
  send(data: ArrayBuffer | string): void;
  close(code?: number, reason?: string): void;
  onopen: ((e: unknown) => void) | null;
  onmessage: ((e: { data: ArrayBuffer | string }) => void) | null;
  onclose: ((e: { code: number }) => void) | null;
  onerror: ((e: unknown) => void) | null;
};

export type AudioIO = {
  onFrame(cb: (frame: ArrayBuffer, rms: number) => void): void;
  onPlayRms(cb: (rms: number) => void): void;
  play(pcm: Uint8Array): void;
  flush(): void;
  close(): void;
};

export type CallDeps = {
  mint: () => Promise<{ token: string; room: string | null }>;
  socket: (url: string) => SocketLike;
  url: (token: string) => string;
  audio: () => Promise<AudioIO>;
  after: (ms: number, fn: () => void) => () => void;
  now: () => number;
  onGateClose?: () => void;
  onNav?: (page: 'deck' | 'core') => void;
  haptic?: (pattern: number | number[]) => void;
  onLive?: (live: boolean) => void;
};

export const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];
export const GIVE_UP_MS = 30000;
export const SILENCE_MS = 15000;
export const CLOSE_CLIENT = 4010;
export const LINK_RESET = '── LINK RESET ──';

const OPEN = 1;
let seq = 0;
const nextId = () => `c${Date.now().toString(36)}${(seq++).toString(36)}`;

/** Lines that do not come from the voice hub (typed commands and replies). */
export function pushCaption(who: 'user' | 'jarvis' | 'system', text: string, extra: { viaRoute?: number; decode?: boolean } = {}) {
  captions.value = { lines: addCaption(captions.value.lines, { id: nextId(), who, text, final: true, ...extra }) };
}

export class CallMachine {
  private ws: SocketLike | null = null;
  private io: AudioIO | null = null;
  private authRetried = false;
  private lossStart: number | null = null;
  private wasLive = false;
  private watchdog: (() => void) | null = null;
  private retry: (() => void) | null = null;
  private ending = false;
  private lastMicUi = 0;

  constructor(private d: CallDeps) {}

  private set(patch: Partial<Call>) {
    call.value = { ...call.value, ...patch };
  }

  get phase() {
    return call.value.phase;
  }

  async start() {
    if (this.phase !== 'idle' && this.phase !== 'error') return;
    this.ending = false;
    this.authRetried = false;
    this.lossStart = null;
    this.wasLive = false;
    this.set({ phase: 'minting', attempt: 0, error: null, agent: 'idle', muted: false, room: null });
    try {
      this.io = await this.d.audio();
    } catch {
      this.fail('MIC BLOCKED');
      return;
    }
    this.io.onFrame((frame, level) => {
      const now = this.d.now();
      if (now - this.lastMicUi > 50) {
        this.lastMicUi = now;
        this.set({ micRms: level });
      }
      if (call.value.phase === 'live' && !call.value.muted && this.ws?.readyState === OPEN) this.ws.send(frame);
    });
    this.io.onPlayRms((level) => this.set({ playRms: level }));
    await this.dial();
  }

  end() {
    if (this.phase === 'idle') return;
    this.ending = true;
    this.set({ phase: 'ending' });
    this.closeSocket(CLOSE_CLIENT);
    this.teardown('idle');
  }

  toggleMute() {
    if (this.phase !== 'live' && this.phase !== 'reconnecting') return;
    this.set({ muted: !call.value.muted });
  }

  /** Typed text during a call goes to lk.chat. */
  sendText(text: string) {
    if (this.ws?.readyState !== OPEN) return false;
    this.ws.send(topic('lk.chat', { text }));
    return true;
  }

  private async dial() {
    if (this.ending) return;
    let minted: { token: string; room: string | null };
    try {
      minted = await this.d.mint();
    } catch (e) {
      if (this.ending) return;
      if (this.lossStart !== null) this.loss();
      else this.fail(errText(e));
      return;
    }
    if (this.ending) return;
    if (this.lossStart === null) this.set({ phase: 'connecting' });
    this.set({ room: minted.room });
    const ws = this.d.socket(this.d.url(minted.token));
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => {
      if (ws !== this.ws) return;
      const recovered = this.lossStart !== null;
      this.lossStart = null;
      this.set({ phase: 'live', attempt: 0, error: null });
      if (recovered) this.caption({ id: nextId(), who: 'system', text: LINK_RESET, final: true });
      if (!this.wasLive || recovered) this.d.haptic?.([10, 60, 20]);
      this.wasLive = true;
      this.d.onLive?.(true);
      this.arm();
    };
    ws.onmessage = (e) => {
      if (ws !== this.ws) return;
      this.arm();
      this.onMessage(e.data);
    };
    ws.onerror = () => {};
    ws.onclose = (e) => {
      if (ws !== this.ws) return;
      this.ws = null;
      this.onClose(e.code);
    };
  }

  private onMessage(data: ArrayBuffer | string) {
    const msg = parse(data);
    if (msg.type === 'audio') {
      this.io?.play(msg.pcm);
      return;
    }
    if (msg.type !== 'topic') return;
    const p = (msg.payload ?? {}) as Record<string, unknown>;
    switch (msg.topic) {
      case 'state': {
        const a = String(p.agent ?? '');
        const agent: Agent = a === 'listening' || a === 'thinking' || a === 'speaking' ? a : 'idle';
        this.set({ agent });
        break;
      }
      case 'caption': {
        if (typeof p.text !== 'string' || !p.text.trim()) break;
        const role = p.role === 'user' ? 'user' : p.role === undefined ? null : 'jarvis';
        const final = p.final === true;
        this.caption({ id: nextId(), who: role === 'user' ? 'user' : 'jarvis', text: p.text.trim(), final });
        if (final && role === 'user') {
          const nav = navWord(p.text);
          if (nav) this.d.onNav?.(nav);
        }
        break;
      }
      case 'flush':
        this.io?.flush();
        break;
      case 'ping':
        this.ws?.send(topic('pong', p));
        break;
      default:
        break; // hello, pong, jarvis-tasks
    }
  }

  private caption(line: Parameters<typeof addCaption>[1]) {
    captions.value = { lines: addCaption(captions.value.lines, line) };
  }

  private onClose(code: number) {
    this.disarm();
    if (this.ending || code === CLOSE_CLIENT) return this.teardown('idle');
    if (code === 4001 || code === 4002) {
      if (!this.authRetried) {
        this.authRetried = true;
        void this.dial();
        return;
      }
      return this.fail('TOKEN REJECTED');
    }
    if (code === 4003) return this.fail('LAPTOP BUSY');
    if (code === 4011) return this.teardown('idle');
    if (code === 4012) return this.fail('DUPLICATE CALL');
    if (code === 4013) {
      this.teardown('idle');
      this.d.onGateClose?.();
      return;
    }
    this.loss();
  }

  private loss() {
    const now = this.d.now();
    if (this.lossStart === null) {
      this.lossStart = now;
      if (this.wasLive) this.d.haptic?.([60]);
    }
    if (now - this.lossStart >= GIVE_UP_MS) return this.fail('LINK LOST');
    const attempt = call.value.attempt;
    this.set({ phase: 'reconnecting', attempt: attempt + 1 });
    this.d.onLive?.(false);
    this.retry = this.d.after(BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)], () => {
      this.retry = null;
      void this.dial();
    });
  }

  private arm() {
    this.disarm();
    this.watchdog = this.d.after(SILENCE_MS, () => {
      this.watchdog = null;
      const ws = this.ws;
      this.ws = null;
      if (ws) {
        ws.onclose = null;
        try {
          ws.close();
        } catch {
          /* already closed */
        }
      }
      this.loss();
    });
  }

  private disarm() {
    this.watchdog?.();
    this.watchdog = null;
  }

  private closeSocket(code: number) {
    this.disarm();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      try {
        ws.close(code, 'bye');
      } catch {
        /* ignore */
      }
    }
  }

  private fail(error: string) {
    this.closeSocket(CLOSE_CLIENT);
    this.teardown('error', error);
    this.d.haptic?.([40, 30, 40]);
  }

  private teardown(phase: 'idle' | 'error', error: string | null = null) {
    this.retry?.();
    this.retry = null;
    this.disarm();
    this.io?.close();
    this.io = null;
    this.lossStart = null;
    this.set({ phase, error, agent: 'idle', micRms: 0, playRms: 0, muted: false, attempt: 0 });
    this.d.onLive?.(false);
  }
}

function errText(e: unknown) {
  const m = e instanceof Error ? e.message : '';
  return m && m.length < 40 ? m.toUpperCase() : 'LAPTOP UNREACHABLE';
}
