/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Transpile the framework-free core and evaluate it in this realm so typed
// arrays compare cleanly with node:assert.
function loadCore() {
  const source = fs.readFileSync(path.join(__dirname, '../lib/voice-session.ts'), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 },
  }).outputText;
  const mod = { exports: {} };
  new Function('exports', 'module', 'require', js)(mod.exports, mod, require);
  return mod.exports;
}
const core = loadCore();
const {
  VoiceSession,
  encodeMicFrame,
  encodeVideoFrame,
  decodeFrame,
  decodeAgentAudio,
  decodeVideoFrame,
  floatToPcm16,
  LinearResampler,
  BlockAccumulator,
  backoffDelay,
  RECONNECT_POLICY,
  FRAME_AGENT_AUDIO,
  MIC_BLOCK_SAMPLES,
  PLAYBACK_JITTER_MS,
  AGENT_SAMPLE_RATE,
} = core;

const settle = () => new Promise((resolve) => setImmediate(resolve));

// ---- fakes ----------------------------------------------------------------

class FakeSocket {
  constructor(url, registry) {
    this.url = url;
    this.readyState = 0;
    this.binaryType = 'blob';
    this.sent = [];
    this.closed = null;
    registry.push(this);
  }
  send(data) {
    this.sent.push(data);
  }
  close(code, reason) {
    this.closed = { code, reason };
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  receive(data) {
    this.onmessage?.({ data });
  }
  topic(topic, payload) {
    this.receive(JSON.stringify({ topic, payload }));
  }
  drop(code) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  binaryFrames() {
    return this.sent.filter((d) => d instanceof ArrayBuffer);
  }
}

function fakeTimers() {
  let pending = [];
  return {
    scheduled: () => pending.slice(),
    setTimeout(fn, ms) {
      const entry = { fn, ms };
      pending.push(entry);
      return entry;
    },
    clearTimeout(entry) {
      pending = pending.filter((e) => e !== entry);
    },
    /** Fire every pending timer, in order. */
    runAll() {
      const due = pending;
      pending = [];
      for (const entry of due) entry.fn();
    },
  };
}

function fakeAudioContext() {
  const ctx = {
    sampleRate: 48000,
    state: 'running',
    currentTime: 0,
    destination: { connect() {}, disconnect() {} },
    sources: [],
    stopCount: 0,
    audioWorklet: { addModule: async () => {} },
    createMediaStreamSource() {
      return { connect() {}, disconnect() {} };
    },
    createAnalyser() {
      return { fftSize: 256, connect() {}, getFloatTimeDomainData: (a) => a.fill(0) };
    },
    createGain() {
      return { gain: { value: 1 }, connect() {}, disconnect() {} };
    },
    createBuffer(channels, length, rate) {
      const data = new Float32Array(length);
      return { duration: length / rate, getChannelData: () => data, data };
    },
    createBufferSource() {
      const source = {
        buffer: null,
        onended: null,
        startedAt: null,
        connect() {},
        start(when) {
          this.startedAt = when;
        },
        stop() {
          ctx.stopCount++;
        },
      };
      ctx.sources.push(source);
      return source;
    },
    resume: async () => {},
  };
  return ctx;
}

function fakeMicStream() {
  const track = {
    enabled: true,
    stopped: false,
    stop() {
      this.stopped = true;
    },
  };
  return {
    track,
    getAudioTracks: () => [track],
    getTracks: () => [track],
  };
}

function harness(options = {}) {
  const sockets = [];
  const timers = fakeTimers();
  const ctx = fakeAudioContext();
  const workletNodes = [];
  const micStream = fakeMicStream();
  const micRequests = [];
  const session = new VoiceSession({
    reconnect: options.reconnect,
    env: {
      timers,
      createSocket: (url) => new FakeSocket(url, sockets),
      createAudioContext: () => ctx,
      createWorkletNode: () => {
        const node = { port: { onmessage: null }, connect() {}, disconnect() {} };
        workletNodes.push(node);
        return node;
      },
      createWorkletUrl: () => 'blob:test-worklet',
      getUserMedia: async (constraints) => {
        micRequests.push(constraints);
        return micStream;
      },
    },
  });
  return { session, sockets, timers, ctx, workletNodes, micStream, micRequests };
}

const URL_BASE = 'ws://127.0.0.1:7777/voice';

function connectSpeaker(h, token = 'tok-1') {
  h.session.connect(URL_BASE, token);
  const socket = h.sockets.at(-1);
  socket.open();
  socket.topic('hello', { identity: 'owner', room: 'desktop', mode: 'speaker' });
  return socket;
}

// ---- frame codec ----------------------------------------------------------

test('mic frame: kind 0x01, 512 LE int16 samples, round-trips', () => {
  const samples = new Int16Array(MIC_BLOCK_SAMPLES);
  samples[0] = 1;
  samples[1] = -2;
  samples[MIC_BLOCK_SAMPLES - 1] = 32767;
  const frame = encodeMicFrame(samples);
  assert.equal(frame.byteLength, 1 + 1024);
  const bytes = new Uint8Array(frame);
  assert.equal(bytes[0], 0x01);
  // Explicit little-endian: sample 1 is bytes [0x01, 0x00] at offset 1.
  assert.equal(bytes[1], 0x01);
  assert.equal(bytes[2], 0x00);
  const decoded = decodeFrame(frame);
  assert.equal(decoded.kind, 0x01);
  assert.equal(decoded.payload.byteLength, 1024);
  assert.throws(() => encodeMicFrame(new Int16Array(10)), RangeError);
});

test('agent audio decode: kind 0x02 payload is LE PCM16, odd lengths rejected', () => {
  const samples = Int16Array.from([0, 100, -100, 32767, -32768]);
  const wire = new ArrayBuffer(1 + samples.length * 2);
  const view = new DataView(wire);
  view.setUint8(0, FRAME_AGENT_AUDIO);
  samples.forEach((s, i) => view.setInt16(1 + i * 2, s, true));
  const frame = decodeFrame(wire);
  assert.equal(frame.kind, FRAME_AGENT_AUDIO);
  assert.deepEqual(Array.from(decodeAgentAudio(frame.payload)), Array.from(samples));
  assert.equal(decodeAgentAudio(new ArrayBuffer(3)), null);
  assert.equal(decodeFrame(new ArrayBuffer(0)), null);
});

test('video frame: [8-byte BE ms timestamp][JPEG] round-trips past 2^32 ms', () => {
  const jpeg = Uint8Array.from([0xff, 0xd8, 0xff, 0xd9]);
  const ts = 2 ** 33 + 5;
  const wire = encodeVideoFrame(ts, jpeg);
  const bytes = new Uint8Array(wire);
  assert.equal(bytes[0], 0x03);
  assert.deepEqual(Array.from(bytes.subarray(1, 9)), [0, 0, 0, 2, 0, 0, 0, 5]);
  const decoded = decodeVideoFrame(decodeFrame(wire).payload);
  assert.equal(decoded.timestampMs, ts);
  assert.deepEqual(Array.from(decoded.jpeg), Array.from(jpeg));
  assert.equal(decodeVideoFrame(new ArrayBuffer(4)), null);
});

test('float to PCM16 clamps and scales symmetrically', () => {
  const out = floatToPcm16(Float32Array.from([-2, -1, 0, 1, 2]));
  assert.deepEqual(Array.from(out), [-32768, -32768, 0, 32767, 32767]);
});

test('resampler: 48 kHz to 16 kHz yields one third of the samples', () => {
  const resampler = new LinearResampler(48000, 16000);
  let total = 0;
  for (let i = 0; i < 100; i++) total += resampler.push(new Float32Array(480)).length;
  assert.ok(Math.abs(total - 16000) <= 2, `got ${total}`);
});

test('block accumulator emits exact 512-sample blocks and keeps the remainder', () => {
  const acc = new BlockAccumulator(MIC_BLOCK_SAMPLES);
  const first = acc.push(new Int16Array(700));
  assert.equal(first.length, 1);
  assert.equal(first[0].length, MIC_BLOCK_SAMPLES);
  const second = acc.push(new Int16Array(324));
  assert.equal(second.length, 1);
});

test('reconnect backoff doubles and is capped', () => {
  const p = RECONNECT_POLICY;
  assert.equal(backoffDelay(0, p), 500);
  assert.equal(backoffDelay(1, p), 1000);
  assert.equal(backoffDelay(2, p), 2000);
  assert.equal(backoffDelay(20, p), p.maxMs);
});

// ---- topic routing --------------------------------------------------------

test('topic routing: subscribers see only their topic and can unsubscribe', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  const tasks = [];
  const other = [];
  const off = h.session.subscribe('jarvis-tasks', (p) => tasks.push(p));
  h.session.subscribe('other', (p) => other.push(p));
  socket.topic('jarvis-tasks', { kind: 'tool_start', tool: 'weather' });
  socket.topic('other', 1);
  assert.deepEqual(tasks, [{ kind: 'tool_start', tool: 'weather' }]);
  assert.deepEqual(other, [1]);
  off();
  socket.topic('jarvis-tasks', { kind: 'log' });
  assert.equal(tasks.length, 1);
});

test('topic routing: state topic maps agent states; unknown states are ignored', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  assert.equal(h.session.getSnapshot().agentState, 'initializing');
  socket.topic('state', { agent: 'speaking', user: 'listening' });
  assert.equal(h.session.getSnapshot().agentState, 'speaking');
  socket.topic('state', { agent: 'idle' });
  assert.equal(h.session.getSnapshot().agentState, 'listening');
  socket.topic('state', { agent: 'banana' });
  assert.equal(h.session.getSnapshot().agentState, 'listening');
});

test('topic routing: captions merge interim into final and split by speaker', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  socket.topic('caption', { text: 'what is the', final: false, from: 'user' });
  socket.topic('caption', { text: 'what is the weather', final: true, from: 'user' });
  socket.topic('caption', { text: 'Sunny.', final: false });
  socket.topic('caption', { text: 'Sunny today.', final: true });
  const messages = h.session.getSnapshot().messages;
  assert.deepEqual(
    messages.map((m) => [m.from, m.text, m.final]),
    [
      ['user', 'what is the weather', true],
      ['agent', 'Sunny today.', true],
    ]
  );
  assert.ok(messages.every((m) => typeof m.id === 'string'));
});

test('malformed, oversize and shape-less text frames are dropped and counted', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  let calls = 0;
  h.session.subscribe('x', () => calls++);
  socket.receive('{not json');
  socket.receive(JSON.stringify({ payload: 1 }));
  socket.receive(JSON.stringify({ topic: 'x', payload: 'a'.repeat(64 * 1024) }));
  assert.equal(calls, 0);
  assert.equal(h.session.dropped, 3);
  socket.topic('x', 'ok');
  assert.equal(calls, 1);
});

test('a throwing subscriber does not stop other subscribers', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  const seen = [];
  const originalError = console.error;
  console.error = () => {};
  try {
    h.session.subscribe('t', () => {
      throw new Error('boom');
    });
    h.session.subscribe('t', (p) => seen.push(p));
    socket.topic('t', 7);
  } finally {
    console.error = originalError;
  }
  assert.deepEqual(seen, [7]);
});

test('watch fires on snapshot changes and getSnapshot is stable between changes', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  let fired = 0;
  const off = h.session.watch(() => fired++);
  const before = h.session.getSnapshot();
  assert.equal(h.session.getSnapshot(), before);
  socket.topic('state', { agent: 'thinking' });
  assert.equal(fired, 1);
  assert.notEqual(h.session.getSnapshot(), before);
  off();
  socket.topic('state', { agent: 'speaking' });
  assert.equal(fired, 1);
});

// ---- observer-mode send suppression ---------------------------------------

test('observer mode: no mic capture and every outbound frame is suppressed', async () => {
  const h = harness();
  h.session.connect(URL_BASE, 'tok-obs');
  const socket = h.sockets[0];
  socket.open();
  socket.topic('hello', { identity: 'hud', room: 'desktop', mode: 'observer' });
  await settle();

  assert.equal(h.micRequests.length, 0, 'observer must not open the microphone');
  assert.equal(h.session.sendChat('hello'), false);
  assert.equal(h.session.publish('anything', { a: 1 }), false);
  assert.equal(h.session.getSnapshot().messages.length, 0);
  socket.topic('ping', 'p1');
  assert.equal(socket.sent.length, 0, 'no pong or text from an observer');
});

test('speaker mode: sendChat sends lk.chat text and records the local message', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  assert.equal(h.session.sendChat('   '), false);
  assert.equal(h.session.sendChat('  open the blinds '), true);
  const sent = JSON.parse(socket.sent.at(-1));
  assert.deepEqual(sent, { topic: 'lk.chat', payload: { text: 'open the blinds' } });
  assert.equal(h.session.getSnapshot().messages.at(-1).from, 'user');
});

test('publish refuses oversize barge payloads and frames over 64 KiB', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  const before = socket.sent.length;
  assert.equal(h.session.publish('jarvis.voice.barge.v1', 'x'.repeat(300)), false);
  assert.equal(h.session.publish('t', 'x'.repeat(65 * 1024)), false);
  assert.equal(socket.sent.length, before);
  assert.equal(h.session.publish('jarvis.voice.barge.v1', { at: 1 }), true);
});

// ---- reconnect policy -----------------------------------------------------

test('no retry for any 4xxx close, including 4001 through 4013', () => {
  for (let code = 4001; code <= 4013; code++) {
    const h = harness();
    h.session.connect(URL_BASE, 'tok');
    h.sockets[0].open();
    h.sockets[0].drop(code);
    assert.equal(h.timers.scheduled().length, 0, `code ${code} must not retry`);
    const snap = h.session.getSnapshot();
    assert.equal(snap.status, 'closed', `code ${code}`);
    assert.equal(snap.closeCode, code);
    assert.equal(h.sockets.length, 1);
  }
});

test('a lost connection retries with exponential backoff, then re-dials the same URL', () => {
  const h = harness();
  h.session.connect(URL_BASE, 'tok-a');
  h.sockets[0].open();
  h.sockets[0].drop(1006);
  assert.equal(h.session.getSnapshot().status, 'reconnecting');
  let [first] = h.timers.scheduled();
  assert.equal(first.ms, 500);
  h.timers.runAll();
  assert.equal(h.sockets.length, 2);
  assert.match(h.sockets[1].url, /token=tok-a/);

  h.sockets[1].drop(1006);
  [first] = h.timers.scheduled();
  assert.equal(first.ms, 1000);
});

test('reconnect is bounded by maxAttempts', () => {
  const h = harness({ reconnect: { maxAttempts: 2 } });
  h.session.connect(URL_BASE, 'tok');
  h.sockets[0].open();
  h.sockets[0].drop(1006);
  h.timers.runAll();
  h.sockets[1].drop(1006);
  h.timers.runAll();
  h.sockets[2].drop(1006);
  assert.equal(h.timers.scheduled().length, 0);
  assert.equal(h.session.getSnapshot().status, 'closed');
  assert.equal(h.sockets.length, 3);
});

test('reconnect with fetchToken mints a fresh token each attempt', async () => {
  const h = harness();
  let n = 0;
  h.session.connect(URL_BASE, 'tok-0', { fetchToken: async () => `tok-${++n}` });
  h.sockets[0].open();
  h.sockets[0].drop(1006);
  h.timers.runAll();
  await settle();
  assert.match(h.sockets[1].url, /token=tok-1/);
});

test('client disconnect closes with 4010 and never reconnects', () => {
  const h = harness();
  connectSpeaker(h);
  h.session.disconnect();
  assert.equal(h.sockets[0].closed.code, 4010);
  assert.equal(h.timers.scheduled().length, 0);
  assert.equal(h.session.getSnapshot().status, 'closed');
  assert.equal(h.session.getSnapshot().isConnected, false);
});

// ---- playback queue and flush --------------------------------------------

test('flush clears the playback queue and stops scheduled sources', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  const pcm = (n) => {
    const view = new DataView(new ArrayBuffer(1 + n * 2));
    view.setUint8(0, FRAME_AGENT_AUDIO);
    return view.buffer;
  };
  // Below the jitter threshold: held, nothing scheduled yet.
  socket.receive(pcm(1000));
  assert.equal(h.ctx.sources.length, 0);
  assert.equal(h.session.playbackQueueLength(), 1);

  // Crossing the threshold starts playback and schedules everything queued.
  const threshold = Math.ceil((PLAYBACK_JITTER_MS / 1000) * AGENT_SAMPLE_RATE);
  socket.receive(pcm(threshold));
  assert.equal(h.ctx.sources.length, 2);
  assert.equal(h.session.playbackQueueLength(), 2);

  socket.topic('flush', null);
  assert.equal(h.ctx.stopCount, 2);
  assert.equal(h.session.playbackQueueLength(), 0);

  // After a flush, new audio waits for the jitter buffer again.
  socket.receive(pcm(500));
  assert.equal(h.ctx.sources.length, 2);
  assert.equal(h.session.playbackQueueLength(), 1);
});

test('closing the socket also drops queued playback', () => {
  const h = harness();
  const socket = connectSpeaker(h);
  socket.receive(
    (() => {
      const buf = new ArrayBuffer(1 + 4000);
      new Uint8Array(buf)[0] = FRAME_AGENT_AUDIO;
      return buf;
    })()
  );
  assert.ok(h.session.playbackQueueLength() > 0);
  socket.drop(1000);
  assert.equal(h.session.playbackQueueLength(), 0);
});

// ---- mic mute -------------------------------------------------------------

test('mute stops mic frames, keeps the socket, and unmute resumes', async () => {
  const h = harness();
  const socket = connectSpeaker(h);
  await settle();
  assert.equal(h.micRequests.length, 1);

  const oneSecond = () => h.workletNodes[0].port.onmessage({ data: new Float32Array(48000) });
  oneSecond();
  const live = socket.binaryFrames().length;
  // 16000 output samples at 16 kHz -> 31 full 32 ms blocks.
  assert.equal(live, 31);
  for (const frame of socket.binaryFrames()) {
    assert.equal(new Uint8Array(frame)[0], 0x01);
    assert.equal(frame.byteLength, 1025);
  }

  h.session.mute();
  assert.equal(h.micStream.track.enabled, false);
  oneSecond();
  assert.equal(socket.binaryFrames().length, live, 'muted: no mic frames');
  assert.equal(socket.readyState, 1, 'socket stays open while muted');
  assert.equal(h.session.sendChat('still here'), true, 'chat still works while muted');

  h.session.unmute();
  assert.equal(h.micStream.track.enabled, true);
  oneSecond();
  assert.equal(socket.binaryFrames().length, live + 31);
});

test('mic capture stops on disconnect', async () => {
  const h = harness();
  connectSpeaker(h);
  await settle();
  h.session.disconnect();
  assert.equal(h.micStream.track.stopped, true);
});

test('echo guard: quiet mic under loud playback is echo, close speech passes', () => {
  const { isEchoBlock, pcmRms } = core;
  assert.equal(isEchoBlock(0.05, 0.3, 0.5), true);
  assert.equal(isEchoBlock(0.2, 0.3, 0.5), false);
  assert.equal(isEchoBlock(0.001, 0.0, 0.5), false);
  assert.equal(isEchoBlock(0.001, 0.3, 0), false);
  assert.equal(pcmRms(new Int16Array(0)), 0);
  assert.ok(Math.abs(pcmRms(Int16Array.from([16384, -16384])) - 0.5) < 1e-6);
});
