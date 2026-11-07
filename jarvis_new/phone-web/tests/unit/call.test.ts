import { beforeEach, describe, expect, it } from 'vitest';
import { call, captions } from '../../src/store';
import { BACKOFF_MS, CallMachine, LINK_RESET, type AudioIO, type SocketLike } from '../../src/voice/call';

class FakeSocket implements SocketLike {
  binaryType = 'blob';
  readyState = 0;
  sent: (ArrayBuffer | string)[] = [];
  closed: number | undefined;
  onopen: SocketLike['onopen'] = null;
  onmessage: SocketLike['onmessage'] = null;
  onclose: SocketLike['onclose'] = null;
  onerror: SocketLike['onerror'] = null;
  constructor(public url: string) {}
  send(d: ArrayBuffer | string) {
    this.sent.push(d);
  }
  close(code?: number) {
    this.closed = code;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  drop(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  msg(data: ArrayBuffer | string) {
    this.onmessage?.({ data });
  }
}

function setup(opts: { mintFails?: number } = {}) {
  let now = 0;
  let timers: { at: number; fn: () => void }[] = [];
  const sockets: FakeSocket[] = [];
  let mints = 0;
  let mintFails = opts.mintFails ?? 0;
  const played: Uint8Array[] = [];
  let flushed = 0;
  let frameCb: ((f: ArrayBuffer, r: number) => void) | null = null;
  let gateClosed = 0;
  const navs: string[] = [];
  const io: AudioIO = {
    onFrame: (cb) => (frameCb = cb),
    onPlayRms: () => {},
    play: (p) => played.push(p),
    flush: () => flushed++,
    close: () => {},
  };
  const m = new CallMachine({
    mint: async () => {
      mints++;
      if (mintFails > 0) {
        mintFails--;
        throw new Error('offline');
      }
      return { token: `t${mints}`, room: `r${mints}` };
    },
    socket: (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s;
    },
    url: (t) => `wss://h/voice?token=${t}`,
    audio: async () => io,
    after: (ms, fn) => {
      const t = { at: now + ms, fn };
      timers.push(t);
      return () => (timers = timers.filter((x) => x !== t));
    },
    now: () => now,
    onGateClose: () => gateClosed++,
    onNav: (p) => navs.push(p),
  });
  const flush = () => new Promise((r) => setTimeout(r, 0));
  const advance = async (ms: number) => {
    const end = now + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const t = timers[0];
      if (!t || t.at > end) break;
      timers.shift();
      now = t.at;
      t.fn();
      await flush();
    }
    now = end;
  };
  return {
    m,
    sockets,
    last: () => sockets[sockets.length - 1],
    mints: () => mints,
    played,
    flushed: () => flushed,
    frame: (f: ArrayBuffer) => frameCb?.(f, 0.3),
    gateClosed: () => gateClosed,
    navs,
    flush,
    advance,
  };
}

async function live() {
  const h = setup();
  await h.m.start();
  await h.flush();
  h.last().open();
  return h;
}

beforeEach(() => {
  call.value = { ...call.value, phase: 'idle', error: null, attempt: 0, muted: false };
  captions.value = { lines: [] };
});

describe('call lifecycle', () => {
  it('mints, connects and goes live', async () => {
    const h = setup();
    const p = h.m.start();
    expect(call.value.phase).toBe('minting');
    await p;
    await h.flush();
    expect(call.value.phase).toBe('connecting');
    expect(h.last().url).toBe('wss://h/voice?token=t1');
    h.last().open();
    expect(call.value.phase).toBe('live');
  });

  it('sends mic frames only while live and unmuted', async () => {
    const h = await live();
    h.frame(new ArrayBuffer(1025));
    expect(h.last().sent).toHaveLength(1);
    h.m.toggleMute();
    h.frame(new ArrayBuffer(1025));
    expect(h.last().sent).toHaveLength(1);
  });

  it('routes state, caption, audio, flush and ping', async () => {
    const h = await live();
    h.last().msg(JSON.stringify({ topic: 'state', payload: { agent: 'speaking' } }));
    expect(call.value.agent).toBe('speaking');
    h.last().msg(JSON.stringify({ topic: 'caption', payload: { text: 'Good evening', final: true } }));
    expect(captions.value.lines.at(-1)).toMatchObject({ who: 'jarvis', text: 'Good evening', final: true });
    h.last().msg(new Uint8Array([2, 9, 9]).buffer);
    expect(h.played).toHaveLength(1);
    h.last().msg(JSON.stringify({ topic: 'flush', payload: {} }));
    expect(h.flushed()).toBe(1);
    h.last().msg(JSON.stringify({ topic: 'ping', payload: { t: 1 } }));
    expect(h.last().sent.at(-1)).toBe(JSON.stringify({ topic: 'pong', payload: { t: 1 } }));
  });

  it('user caption saying deck navigates; role-less captions never do', async () => {
    const h = await live();
    h.last().msg(JSON.stringify({ topic: 'caption', payload: { text: 'deck', final: true } }));
    expect(h.navs).toEqual([]);
    h.last().msg(JSON.stringify({ topic: 'caption', payload: { text: 'open the deck', final: true, role: 'user' } }));
    expect(h.navs).toEqual(['deck']);
  });

  it('typed text during a call goes to lk.chat', async () => {
    const h = await live();
    expect(h.m.sendText('hello')).toBe(true);
    expect(h.last().sent.at(-1)).toBe(JSON.stringify({ topic: 'lk.chat', payload: { text: 'hello' } }));
  });

  it('end closes with 4010', async () => {
    const h = await live();
    const s = h.last();
    h.m.end();
    expect(s.closed).toBe(4010);
    expect(call.value.phase).toBe('idle');
  });
});

describe('close code table', () => {
  const table: [number, string, string | null][] = [
    [4003, 'error', 'LAPTOP BUSY'],
    [4011, 'idle', null],
    [4012, 'error', 'DUPLICATE CALL'],
    [4013, 'idle', null],
  ];
  for (const [code, phase, error] of table) {
    it(`${code} → ${phase}`, async () => {
      const h = await live();
      h.last().drop(code);
      await h.flush();
      expect(call.value.phase).toBe(phase);
      expect(call.value.error).toBe(error);
      expect(h.mints()).toBe(1); // no retry
      if (code === 4013) expect(h.gateClosed()).toBe(1);
    });
  }

  for (const code of [4001, 4002]) {
    it(`${code} re-mints once, then errors`, async () => {
      const h = await live();
      h.last().drop(code);
      await h.flush();
      expect(h.mints()).toBe(2);
      h.last().drop(code);
      await h.flush();
      expect(call.value.phase).toBe('error');
      expect(h.mints()).toBe(2);
    });
  }

  it('network loss reconnects with backoff and adds LINK RESET', async () => {
    const h = await live();
    h.last().drop(1006);
    expect(call.value.phase).toBe('reconnecting');
    await h.advance(BACKOFF_MS[0] - 1);
    expect(h.mints()).toBe(1);
    await h.advance(1);
    expect(h.mints()).toBe(2);
    h.last().drop(1006);
    await h.advance(BACKOFF_MS[1]);
    expect(h.mints()).toBe(3);
    h.last().open();
    expect(call.value.phase).toBe('live');
    expect(captions.value.lines.at(-1)!.text).toBe(LINK_RESET);
  });

  it('gives up after 30 s of failed reconnects', async () => {
    const h = setup();
    await h.m.start();
    await h.flush();
    h.last().open();
    h.last().drop(1006);
    // every later mint fails
    for (let i = 0; i < 20 && call.value.phase !== 'error'; i++) {
      await h.advance(8000);
      if (h.last().readyState === 0) h.last().drop(1006);
      await h.flush();
    }
    expect(call.value.phase).toBe('error');
    expect(call.value.error).toBe('LINK LOST');
  });

  it('15 s of silence counts as a lost link', async () => {
    const h = await live();
    await h.advance(15000);
    expect(call.value.phase).toBe('reconnecting');
  });
});
