/**
 * Framework-free voice transport client for the Jarvis WebSocket protocol.
 * Spec: docs/voice-transport.md. Replaces livekit-client's Room for the
 * voice path. Nothing here touches the DOM or React at import time; every
 * browser/timer dependency is reachable through `VoiceEnv` so tests can
 * inject fakes.
 */

// ---------------------------------------------------------------------------
// Protocol constants
// ---------------------------------------------------------------------------

export const FRAME_MIC = 0x01;
export const FRAME_AGENT_AUDIO = 0x02;
export const FRAME_VIDEO = 0x03;

export const MIC_SAMPLE_RATE = 16000;
export const AGENT_SAMPLE_RATE = 24000;
/** 32 ms at 16 kHz. Encoded frame is 1 + 1024 bytes. */
export const MIC_BLOCK_SAMPLES = 512;
export const MAX_FRAME_BYTES = 64 * 1024;
export const BARGE_TOPIC = 'jarvis.voice.barge.v1';
export const BARGE_MAX_BYTES = 256;
export const CHAT_TOPIC = 'lk.chat';
/** Playback is held until this much agent audio is queued, then started. */
export const PLAYBACK_JITTER_MS = 120;

/** Close codes from docs/voice-transport.md. Every 4xxx close is terminal. */
export const CLOSE = {
  BAD_TOKEN: 4001,
  REPLAYED_TOKEN: 4002,
  CALL_ALREADY_ACTIVE: 4003,
  OBSERVER_FORBIDDEN_FRAME: 4004,
  CLIENT_REQUESTED: 4010,
  ROOM_CLOSED: 4011,
  DUPLICATE_IDENTITY: 4012,
  REVOKED: 4013,
} as const;

const CLOSE_REASONS: Record<number, string> = {
  [CLOSE.BAD_TOKEN]: 'Voice token rejected or expired',
  [CLOSE.REPLAYED_TOKEN]: 'Voice token already used',
  [CLOSE.CALL_ALREADY_ACTIVE]: 'A voice call is already active',
  [CLOSE.OBSERVER_FORBIDDEN_FRAME]: 'Observer sent a forbidden frame',
  [CLOSE.CLIENT_REQUESTED]: 'Voice call ended',
  [CLOSE.ROOM_CLOSED]: 'Voice room closed',
  [CLOSE.DUPLICATE_IDENTITY]: 'Duplicate voice identity',
  [CLOSE.REVOKED]: 'Voice access revoked',
};

export const RECONNECT_POLICY = {
  maxAttempts: 6,
  baseMs: 500,
  maxMs: 8000,
} as const;

export type ReconnectPolicy = { maxAttempts: number; baseMs: number; maxMs: number };
export type VoiceMode = 'speaker' | 'observer';
export type VoiceStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'closed';
export type AgentState = 'initializing' | 'listening' | 'thinking' | 'speaking';
export type TranscriptMessage = {
  id: string;
  from: 'user' | 'agent';
  text: string;
  final: boolean;
};

export type VoiceSnapshot = {
  status: VoiceStatus;
  isConnected: boolean;
  mode: VoiceMode | null;
  identity: string | null;
  room: string | null;
  agentState: AgentState;
  muted: boolean;
  playbackMuted: boolean;
  messages: TranscriptMessage[];
  closeCode: number | null;
  lastError: string | null;
};

export type ConnectOptions = {
  /**
   * Called before each reconnect attempt to mint a fresh token. The server's
   * token nonce is single use, so a reconnect with the old token is refused
   * (4002). Without this, a dropped connection is not retried.
   */
  fetchToken?: () => Promise<string>;
};

/** Minimal socket surface used by the client (WebSocket-compatible). */
export type SocketLike = Pick<
  WebSocket,
  'binaryType' | 'readyState' | 'send' | 'close' | 'onopen' | 'onmessage' | 'onclose' | 'onerror'
>;

export type VoiceEnv = {
  timers: {
    setTimeout(fn: () => void, ms: number): unknown;
    clearTimeout(id: unknown): void;
  };
  createSocket(url: string): SocketLike;
  createAudioContext(): AudioContext;
  createWorkletNode(ctx: AudioContext, name: string): AudioWorkletNode;
  createWorkletUrl(): string;
  getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream>;
};

export type VoiceSessionOptions = {
  env?: Partial<VoiceEnv>;
  reconnect?: Partial<ReconnectPolicy>;
};

const SOCKET_OPEN = 1;
const MAX_MESSAGES = 200;

// ---------------------------------------------------------------------------
// Frame codec (pure)
// ---------------------------------------------------------------------------

/** Float samples in [-1, 1] to little-endian PCM16 samples (clamped). */
export function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = Math.round(s < 0 ? s * 0x8000 : s * 0x7fff);
  }
  return out;
}

/** Build a binary frame: `[kind][payload]`. */
export function encodeFrame(kind: number, payload: ArrayBufferView | ArrayBuffer): ArrayBuffer {
  const bytes =
    payload instanceof ArrayBuffer
      ? new Uint8Array(payload)
      : new Uint8Array(payload.buffer, payload.byteOffset, payload.byteLength);
  const out = new Uint8Array(1 + bytes.byteLength);
  out[0] = kind;
  out.set(bytes, 1);
  return out.buffer;
}

/** Mic block (16 kHz PCM16 LE, 512 samples) as a kind 0x01 frame. */
export function encodeMicFrame(samples: Int16Array): ArrayBuffer {
  if (samples.length !== MIC_BLOCK_SAMPLES) {
    throw new RangeError(`mic block must be ${MIC_BLOCK_SAMPLES} samples`);
  }
  const out = new ArrayBuffer(1 + samples.length * 2);
  const view = new DataView(out);
  view.setUint8(0, FRAME_MIC);
  for (let i = 0; i < samples.length; i++) view.setInt16(1 + i * 2, samples[i], true);
  return out;
}

/** Video frame `[8-byte BE ms timestamp][JPEG]` as a kind 0x03 frame. */
export function encodeVideoFrame(timestampMs: number, jpeg: Uint8Array): ArrayBuffer {
  const out = new Uint8Array(1 + 8 + jpeg.byteLength);
  const view = new DataView(out.buffer);
  view.setUint8(0, FRAME_VIDEO);
  // Split into two 32-bit halves so millisecond timestamps need no BigInt.
  const hi = Math.floor(timestampMs / 0x100000000);
  const lo = timestampMs >>> 0;
  view.setUint32(1, hi >>> 0, false);
  view.setUint32(5, lo, false);
  out.set(jpeg, 9);
  return out.buffer;
}

export type DecodedFrame = { kind: number; payload: ArrayBuffer };

/** Split a binary frame into kind and payload. Returns null when empty. */
export function decodeFrame(data: ArrayBuffer): DecodedFrame | null {
  if (data.byteLength < 1) return null;
  const kind = new Uint8Array(data, 0, 1)[0];
  return { kind, payload: data.slice(1) };
}

/** Kind 0x02 payload as PCM16 samples. Null for odd-length payloads. */
export function decodeAgentAudio(payload: ArrayBuffer): Int16Array | null {
  if (payload.byteLength % 2 !== 0) return null;
  const view = new DataView(payload);
  const out = new Int16Array(payload.byteLength / 2);
  for (let i = 0; i < out.length; i++) out[i] = view.getInt16(i * 2, true);
  return out;
}

/** Kind 0x03 payload split into timestamp and JPEG bytes. */
export function decodeVideoFrame(
  payload: ArrayBuffer
): { timestampMs: number; jpeg: Uint8Array } | null {
  if (payload.byteLength < 8) return null;
  const view = new DataView(payload);
  const hi = view.getUint32(0, false);
  const lo = view.getUint32(4, false);
  return {
    timestampMs: hi * 0x100000000 + lo,
    jpeg: new Uint8Array(payload, 8),
  };
}

// ---------------------------------------------------------------------------
// Audio helpers (pure)
// ---------------------------------------------------------------------------

/** Streaming linear resampler. Keeps fractional phase and tail between calls. */
export class LinearResampler {
  private readonly ratio: number;
  private tail: Float32Array = new Float32Array(0);
  private phase = 0;

  constructor(inRate: number, outRate: number) {
    this.ratio = inRate / outRate;
  }

  push(input: Float32Array): Float32Array {
    const buf = new Float32Array(this.tail.length + input.length);
    buf.set(this.tail, 0);
    buf.set(input, this.tail.length);
    const out: number[] = [];
    let p = this.phase;
    while (p + 1 < buf.length) {
      const i = Math.floor(p);
      const f = p - i;
      out.push(buf[i] * (1 - f) + buf[i + 1] * f);
      p += this.ratio;
    }
    const keep = Math.floor(p);
    this.tail = buf.slice(keep);
    this.phase = p - keep;
    return Float32Array.from(out);
  }
}

/** Accumulates PCM16 samples and emits exact fixed-size blocks. */
export class BlockAccumulator {
  private readonly block = new Int16Array(MIC_BLOCK_SAMPLES);
  private fill = 0;

  constructor(private readonly size: number = MIC_BLOCK_SAMPLES) {}

  push(samples: Int16Array): Int16Array[] {
    const out: Int16Array[] = [];
    let offset = 0;
    while (offset < samples.length) {
      const take = Math.min(this.size - this.fill, samples.length - offset);
      this.block.set(samples.subarray(offset, offset + take), this.fill);
      this.fill += take;
      offset += take;
      if (this.fill === this.size) {
        out.push(this.block.slice());
        this.fill = 0;
      }
    }
    return out;
  }
}

/** RMS of the current analyser window, clamped to [0, 1]. */
export function rmsLevel(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(sum / samples.length));
}

// ---------------------------------------------------------------------------
// Mic worklet (inline) and environment defaults
// ---------------------------------------------------------------------------

const MIC_WORKLET_SOURCE = `
class JarvisMic extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor('jarvis-mic', JarvisMic);
`;

function defaultEnv(): VoiceEnv {
  return {
    timers: {
      setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
      clearTimeout: (id) => globalThis.clearTimeout(id as ReturnType<typeof setTimeout>),
    },
    createSocket: (url) => new WebSocket(url),
    createAudioContext: () => {
      const Ctor =
        globalThis.AudioContext ??
        (globalThis as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) throw new Error('Web Audio is not available');
      return new Ctor();
    },
    createWorkletNode: (ctx, name) => new AudioWorkletNode(ctx, name),
    createWorkletUrl: () =>
      URL.createObjectURL(new Blob([MIC_WORKLET_SOURCE], { type: 'text/javascript' })),
    getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  };
}

function byteLength(text: string): number {
  return new TextEncoder().encode(text).byteLength;
}

/** Codes 4000-4999 are server verdicts: retrying cannot change them. */
export function isTerminalClose(code: number): boolean {
  return code >= 4000 && code <= 4999;
}

/** Exponential backoff: base * 2^attempt, capped at maxMs. */
export function backoffDelay(attempt: number, policy: ReconnectPolicy): number {
  return Math.min(policy.maxMs, policy.baseMs * 2 ** attempt);
}

// ---------------------------------------------------------------------------
// VoiceSession
// ---------------------------------------------------------------------------

type TopicHandler = (payload: unknown) => void;
type MicState = {
  stream: MediaStream;
  ctx: AudioContext;
  node: AudioWorkletNode;
  source: MediaStreamAudioSourceNode;
  analyser: AnalyserNode;
  resampler: LinearResampler;
  blocks: BlockAccumulator;
  sink: GainNode;
};
type PlaybackState = {
  ctx: AudioContext;
  gain: GainNode;
  analyser: AnalyserNode;
  started: boolean;
  nextTime: number;
  pending: Float32Array[];
  pendingSamples: number;
  sources: Set<AudioBufferSourceNode>;
};

export class VoiceSession {
  private readonly env: VoiceEnv;
  private readonly policy: ReconnectPolicy;
  private readonly topics = new Map<string, Set<TopicHandler>>();
  private readonly watchers = new Set<() => void>();

  private socket: SocketLike | null = null;
  private url: string | null = null;
  private token: string | null = null;
  private fetchToken: (() => Promise<string>) | null = null;
  private intentional = false;
  private attempts = 0;
  private retryTimer: unknown = null;

  private ctx: AudioContext | null = null;
  private mic: MicState | null = null;
  private micGeneration = 0;
  private playback: PlaybackState | null = null;
  private playbackMuted = false;
  private playbackVolume = 1;

  private muted = false;
  private droppedFrames = 0;
  private snap: VoiceSnapshot = {
    status: 'idle',
    isConnected: false,
    mode: null,
    identity: null,
    room: null,
    agentState: 'initializing',
    muted: false,
    playbackMuted: false,
    messages: [],
    closeCode: null,
    lastError: null,
  };
  private nextMessageId = 1;

  constructor(options: VoiceSessionOptions = {}) {
    this.env = { ...defaultEnv(), ...options.env };
    this.policy = { ...RECONNECT_POLICY, ...options.reconnect };
  }

  // -- store (React-friendly: stable functions, immutable snapshots) --------

  /** Subscribe to snapshot changes. Returns an unsubscribe function. */
  watch = (listener: () => void): (() => void) => {
    this.watchers.add(listener);
    return () => {
      this.watchers.delete(listener);
    };
  };

  getSnapshot = (): VoiceSnapshot => this.snap;

  private patch(next: Partial<VoiceSnapshot>): void {
    this.snap = {
      ...this.snap,
      ...next,
      isConnected: (next.status ?? this.snap.status) === 'connected',
    };
    for (const fn of [...this.watchers]) fn();
  }

  // -- topics ---------------------------------------------------------------

  /** Subscribe to a JSON topic. Returns an unsubscribe function. */
  subscribe(topic: string, handler: TopicHandler): () => void {
    let set = this.topics.get(topic);
    if (!set) {
      set = new Set();
      this.topics.set(topic, set);
    }
    set.add(handler);
    return () => {
      set.delete(handler);
      if (set.size === 0 && this.topics.get(topic) === set) this.topics.delete(topic);
    };
  }

  /** Frames dropped for size, parse or shape (telemetry). */
  get dropped(): number {
    return this.droppedFrames;
  }

  // -- lifecycle -------------------------------------------------------------

  /**
   * Open a call. `url` is the endpoint without a token; the token is added
   * as the `token` query parameter.
   */
  connect(url: string, token: string, options: ConnectOptions = {}): void {
    if (this.socket) this.disconnect();
    this.url = url;
    this.token = token;
    this.fetchToken = options.fetchToken ?? null;
    this.intentional = false;
    this.attempts = 0;
    this.patch({ closeCode: null, lastError: null });
    this.open();
  }

  /** End the call. Sends close 4010 and does not reconnect. */
  disconnect(): void {
    this.intentional = true;
    this.clearRetry();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      this.detach(socket);
      try {
        socket.close(CLOSE.CLIENT_REQUESTED, 'client');
      } catch {
        /* already closed */
      }
    }
    this.teardownMedia();
    this.patch({ status: 'closed', mode: null, agentState: 'initializing' });
  }

  /** Browsers need a user gesture before audio can play. Call from a click. */
  async resumeAudio(): Promise<void> {
    const ctx = this.ensureContext();
    if (ctx.state === 'suspended') await ctx.resume();
  }

  /** Stop sending mic audio. The socket stays open. */
  mute(): void {
    this.setMuted(true);
  }

  unmute(): void {
    this.setMuted(false);
  }

  setMuted(muted: boolean): void {
    if (this.muted === muted) return;
    this.muted = muted;
    const track = this.mic?.stream.getAudioTracks()[0];
    if (track) track.enabled = !muted;
    this.patch({ muted });
  }

  /** Silence agent playback locally (the server still streams it). */
  setPlaybackMuted(muted: boolean): void {
    this.playbackMuted = muted;
    this.applyPlaybackGain();
    this.patch({ playbackMuted: muted });
  }

  setPlaybackVolume(volume: number): void {
    this.playbackVolume = Math.max(0, Math.min(1, volume));
    this.applyPlaybackGain();
  }

  /** Send a chat line on `lk.chat`. Returns false when nothing was sent. */
  sendChat(text: string): boolean {
    const trimmed = text.trim();
    if (!trimmed) return false;
    if (!this.publish(CHAT_TOPIC, { text: trimmed })) return false;
    this.appendMessage({ from: 'user', text: trimmed, final: true });
    return true;
  }

  /**
   * Send a JSON topic frame. Refused (false) in observer mode, when the
   * socket is not open, when the frame is over 64 KiB, or when a barge
   * payload exceeds 256 bytes.
   */
  publish(topic: string, payload: unknown): boolean {
    if (this.snap.mode === 'observer') return false;
    const socket = this.socket;
    if (!socket || socket.readyState !== SOCKET_OPEN) return false;
    const text = JSON.stringify({ topic, payload });
    if (byteLength(text) > MAX_FRAME_BYTES) return false;
    if (topic === BARGE_TOPIC && byteLength(JSON.stringify(payload)) > BARGE_MAX_BYTES) {
      return false;
    }
    socket.send(text);
    return true;
  }

  /** Current playback level (agent output) or mic level, 0..1 RMS. */
  level(source: 'agent' | 'mic' = 'agent'): number {
    const analyser = source === 'mic' ? this.mic?.analyser : this.playback?.analyser;
    if (!analyser) return 0;
    const buf = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(buf);
    return rmsLevel(buf);
  }

  /** Agent audio queued but not yet finished: pending chunks + scheduled sources. */
  playbackQueueLength(): number {
    if (!this.playback) return 0;
    return this.playback.pending.length + this.playback.sources.size;
  }

  // -- socket ----------------------------------------------------------------

  private open(): void {
    if (!this.url || !this.token) return;
    const target = new URL(this.url);
    target.searchParams.set('token', this.token);
    this.patch({ status: this.attempts > 0 ? 'reconnecting' : 'connecting' });

    const socket = this.env.createSocket(target.toString());
    socket.binaryType = 'arraybuffer';
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.patch({ status: 'connected' });
    };
    socket.onmessage = (event: MessageEvent) => {
      if (this.socket !== socket) return;
      if (typeof event.data === 'string') this.handleText(event.data);
      else if (event.data instanceof ArrayBuffer) this.handleBinary(event.data);
      else this.droppedFrames++;
    };
    socket.onerror = () => {
      if (this.socket !== socket) return;
      this.patch({ lastError: 'Voice connection error' });
    };
    socket.onclose = (event: CloseEvent) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.detach(socket);
      this.handleClose(event.code);
    };
  }

  private detach(socket: SocketLike): void {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
  }

  private handleClose(code: number): void {
    this.teardownMedia();
    this.patch({ mode: null, agentState: 'initializing', closeCode: code });
    if (this.intentional) {
      this.patch({ status: 'closed' });
      return;
    }
    if (isTerminalClose(code)) {
      this.patch({
        status: 'closed',
        lastError: CLOSE_REASONS[code] ?? `Voice call closed (${code})`,
      });
      return;
    }
    if (this.attempts >= this.policy.maxAttempts) {
      this.patch({ status: 'closed', lastError: 'Voice connection lost' });
      return;
    }
    const delay = backoffDelay(this.attempts, this.policy);
    this.attempts += 1;
    this.patch({ status: 'reconnecting', lastError: 'Voice connection lost' });
    this.clearRetry();
    this.retryTimer = this.env.timers.setTimeout(() => {
      this.retryTimer = null;
      if (this.intentional) return;
      if (!this.fetchToken) {
        this.open();
        return;
      }
      this.fetchToken().then(
        (token) => {
          if (this.intentional) return;
          this.token = token;
          this.open();
        },
        () => this.patch({ status: 'closed', lastError: 'Could not renew voice token' })
      );
    }, delay);
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      this.env.timers.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  // -- inbound ---------------------------------------------------------------

  private handleText(text: string): void {
    if (byteLength(text) > MAX_FRAME_BYTES) {
      this.droppedFrames++;
      return;
    }
    let msg: unknown;
    try {
      msg = JSON.parse(text);
    } catch {
      this.droppedFrames++;
      return;
    }
    const frame = msg as { topic?: unknown; payload?: unknown } | null;
    if (!frame || typeof frame !== 'object' || typeof frame.topic !== 'string') {
      this.droppedFrames++;
      return;
    }
    this.route(frame.topic, frame.payload);
  }

  /** Built-in topics update session state first, then fan out to subscribers. */
  route(topic: string, payload: unknown): void {
    switch (topic) {
      case 'hello':
        this.onHello(payload);
        break;
      case 'state':
        this.onState(payload);
        break;
      case 'caption':
        this.onCaption(payload);
        break;
      case 'flush':
        this.flushPlayback();
        break;
      case 'ping':
        this.publishRaw('pong', payload);
        break;
      default:
        break;
    }
    const handlers = this.topics.get(topic);
    if (!handlers) return;
    for (const handler of [...handlers]) {
      try {
        handler(payload);
      } catch (error) {
        console.error(`[voice] topic handler failed: ${topic}`, error);
      }
    }
  }

  private handleBinary(buffer: ArrayBuffer): void {
    if (buffer.byteLength > MAX_FRAME_BYTES) {
      this.droppedFrames++;
      return;
    }
    const frame = decodeFrame(buffer);
    if (!frame || frame.kind !== FRAME_AGENT_AUDIO) {
      this.droppedFrames++;
      return;
    }
    const samples = decodeAgentAudio(frame.payload);
    if (!samples) {
      this.droppedFrames++;
      return;
    }
    this.pushAgentAudio(samples);
  }

  private onHello(payload: unknown): void {
    const hello = (payload ?? {}) as { identity?: unknown; room?: unknown; mode?: unknown };
    const mode: VoiceMode = hello.mode === 'observer' ? 'observer' : 'speaker';
    this.attempts = 0;
    this.patch({
      status: 'connected',
      mode,
      identity: typeof hello.identity === 'string' ? hello.identity : null,
      room: typeof hello.room === 'string' ? hello.room : null,
    });
    if (mode === 'speaker') {
      this.startMic().catch((error: unknown) => {
        console.error('[voice] microphone failed', error);
        this.patch({ lastError: 'Microphone unavailable' });
      });
    }
  }

  private onState(payload: unknown): void {
    const agent = (payload as { agent?: unknown } | null)?.agent;
    const next = mapAgentState(agent);
    if (next && next !== this.snap.agentState) this.patch({ agentState: next });
  }

  private onCaption(payload: unknown): void {
    const caption = payload as { text?: unknown; final?: unknown; from?: unknown } | null;
    if (!caption || typeof caption.text !== 'string') return;
    const text = caption.text;
    const final = caption.final === true;
    const from: 'user' | 'agent' = caption.from === 'user' ? 'user' : 'agent';
    if (!text.trim() && !final) return;

    const messages = this.snap.messages.slice();
    const last = messages.at(-1);
    if (last && !last.final && last.from === from) {
      messages[messages.length - 1] = { ...last, text, final };
    } else {
      if (last && !last.final) messages[messages.length - 1] = { ...last, final: true };
      messages.push({ id: `m${this.nextMessageId++}`, from, text, final });
    }
    this.patch({ messages: messages.slice(-MAX_MESSAGES) });
  }

  private appendMessage(message: Omit<TranscriptMessage, 'id'>): void {
    const messages = [...this.snap.messages, { ...message, id: `m${this.nextMessageId++}` }];
    this.patch({ messages: messages.slice(-MAX_MESSAGES) });
  }

  // -- outbound --------------------------------------------------------------

  private publishRaw(topic: string, payload: unknown): void {
    if (this.snap.mode === 'observer') return;
    const socket = this.socket;
    if (!socket || socket.readyState !== SOCKET_OPEN) return;
    socket.send(JSON.stringify({ topic, payload }));
  }

  private sendMicBlock(block: Int16Array): void {
    const socket = this.socket;
    if (!socket || socket.readyState !== SOCKET_OPEN) return;
    if (this.snap.mode !== 'speaker' || this.muted) return;
    socket.send(encodeMicFrame(block));
  }

  // -- mic -------------------------------------------------------------------

  private async startMic(): Promise<void> {
    if (this.mic) return;
    const generation = ++this.micGeneration;
    const stream = await this.env.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (generation !== this.micGeneration || this.snap.mode !== 'speaker') {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    const ctx = this.ensureContext();
    const url = this.env.createWorkletUrl();
    try {
      await ctx.audioWorklet.addModule(url);
    } catch (error) {
      stream.getTracks().forEach((t) => t.stop());
      this.patch({ lastError: 'Microphone unavailable' });
      console.error('[voice] mic worklet failed', error);
      return;
    } finally {
      URL.revokeObjectURL(url);
    }
    if (generation !== this.micGeneration || this.snap.mode !== 'speaker') {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }

    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    const node = this.env.createWorkletNode(ctx, 'jarvis-mic');
    // A zero-gain sink keeps the worklet pulled without audible echo.
    const sink = ctx.createGain();
    sink.gain.value = 0;
    source.connect(analyser);
    source.connect(node);
    node.connect(sink);
    sink.connect(ctx.destination);

    const resampler = new LinearResampler(ctx.sampleRate, MIC_SAMPLE_RATE);
    const blocks = new BlockAccumulator(MIC_BLOCK_SAMPLES);
    node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      const pcm = floatToPcm16(resampler.push(event.data));
      for (const block of blocks.push(pcm)) this.sendMicBlock(block);
    };
    this.mic = { stream, ctx, node, source, analyser, resampler, blocks, sink };
    const track = stream.getAudioTracks()[0];
    if (track) track.enabled = !this.muted;
  }

  private stopMic(): void {
    this.micGeneration++;
    const mic = this.mic;
    if (!mic) return;
    this.mic = null;
    mic.node.port.onmessage = null;
    try {
      mic.source.disconnect();
      mic.node.disconnect();
    } catch {
      /* already disconnected */
    }
    mic.stream.getTracks().forEach((t) => t.stop());
  }

  // -- playback --------------------------------------------------------------

  private ensureContext(): AudioContext {
    if (!this.ctx) this.ctx = this.env.createAudioContext();
    return this.ctx;
  }

  private ensurePlayback(): PlaybackState {
    if (this.playback) return this.playback;
    const ctx = this.ensureContext();
    const gain = ctx.createGain();
    const analyser = ctx.createAnalyser();
    gain.connect(analyser);
    analyser.connect(ctx.destination);
    this.playback = {
      ctx,
      gain,
      analyser,
      started: false,
      nextTime: 0,
      pending: [],
      pendingSamples: 0,
      sources: new Set(),
    };
    this.applyPlaybackGain();
    return this.playback;
  }

  private applyPlaybackGain(): void {
    if (!this.playback) return;
    this.playback.gain.gain.value = this.playbackMuted ? 0 : this.playbackVolume;
  }

  private pushAgentAudio(samples: Int16Array): void {
    const playback = this.ensurePlayback();
    const float = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i++) float[i] = samples[i] / 0x8000;

    if (!playback.started) {
      playback.pending.push(float);
      playback.pendingSamples += float.length;
      if ((playback.pendingSamples / AGENT_SAMPLE_RATE) * 1000 < PLAYBACK_JITTER_MS) return;
      playback.started = true;
      playback.nextTime = playback.ctx.currentTime + 0.02;
      const queued = playback.pending;
      playback.pending = [];
      playback.pendingSamples = 0;
      for (const chunk of queued) this.schedule(playback, chunk);
      return;
    }
    this.schedule(playback, float);
  }

  private schedule(playback: PlaybackState, samples: Float32Array): void {
    const { ctx } = playback;
    const buffer = ctx.createBuffer(1, samples.length, AGENT_SAMPLE_RATE);
    buffer.getChannelData(0).set(samples);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(playback.gain);
    const when = Math.max(playback.nextTime, ctx.currentTime + 0.01);
    source.onended = () => {
      playback.sources.delete(source);
    };
    source.start(when);
    playback.nextTime = when + buffer.duration;
    playback.sources.add(source);
  }

  /** Drop every queued and scheduled agent chunk now (server `flush`). */
  flushPlayback(): void {
    const playback = this.playback;
    if (!playback) return;
    for (const source of playback.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        /* never started */
      }
    }
    playback.sources.clear();
    playback.pending = [];
    playback.pendingSamples = 0;
    playback.started = false;
    playback.nextTime = 0;
  }

  private teardownMedia(): void {
    this.stopMic();
    this.flushPlayback();
  }
}

function mapAgentState(raw: unknown): AgentState | null {
  switch (raw) {
    case 'initializing':
      return 'initializing';
    case 'idle':
    case 'listening':
      return 'listening';
    case 'thinking':
      return 'thinking';
    case 'speaking':
      return 'speaking';
    default:
      return null;
  }
}
