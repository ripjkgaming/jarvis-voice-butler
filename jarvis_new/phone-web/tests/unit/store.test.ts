import { describe, expect, it } from 'vitest';
import { ApiError, api, classify } from '../../src/api';
import { BACKOFF_MS, Poller } from '../../src/poll';
import {
  addCaption,
  auth,
  emptyLaptop,
  fmtAge,
  glyphFor,
  laptop,
  laptopFromControl,
  laptopFromState,
  link,
  navWord,
  stripLamps,
  type Caption,
} from '../../src/store';

const STATE = {
  ok: true,
  laptop: {
    host: 'ripjk',
    guest: true,
    locked: false,
    screens: 'on' as const,
    mic_muted: null,
    presence: 'present',
    power: { percent: 87, charging: true },
  },
  voice: { call_live: false, voice_owner: null, agent_state: null, waking: false },
  gate: { state: 'open', expires_in_s: 100, soft_in_s: 600 },
  ages: { locked: 2, screens: 3, mic_muted: null, presence: 20 },
};

describe('reducers', () => {
  it('laptopFromState maps values and ages', () => {
    const l = laptopFromState(STATE, 100_000);
    expect(l.guest).toEqual({ value: true, at: 100_000 });
    expect(l.locked).toEqual({ value: false, at: 98_000 });
    expect(l.screens.value).toBe('on');
    expect(l.micMuted).toEqual({ value: null, at: 0 });
    expect(l.presence).toEqual({ value: 'present', at: 80_000 });
    expect(l.power.value).toEqual({ percent: 87, charging: true });
  });

  it('control readback keeps prior values for nulls', () => {
    const prev = laptopFromState(STATE, 1000);
    const next = laptopFromControl(prev, { ...STATE.laptop, locked: true, screens: null }, 5000);
    expect(next.locked).toEqual({ value: true, at: 5000 });
    expect(next.screens).toBe(prev.screens);
  });

  it('captions replace a trailing partial and cap at 50', () => {
    let lines: Caption[] = [];
    lines = addCaption(lines, { id: 'a', who: 'user', text: 'he', final: false });
    lines = addCaption(lines, { id: 'b', who: 'user', text: 'hello', final: true });
    expect(lines).toEqual([{ id: 'a', who: 'user', text: 'hello', final: true }]);
    lines = addCaption(lines, { id: 'c', who: 'jarvis', text: 'Sir', final: false });
    lines = addCaption(lines, { id: 'd', who: 'user', text: 'x', final: true });
    expect(lines.map((l) => l.text)).toEqual(['hello', 'Sir', 'x']);
    for (let i = 0; i < 60; i++) lines = addCaption(lines, { id: `n${i}`, who: 'user', text: `${i}`, final: true });
    expect(lines).toHaveLength(50);
    expect(lines[49].text).toBe('59');
  });

  it('glyph mapping with locked override', () => {
    expect(glyphFor(true, 'speaking')).toBe('locked');
    expect(glyphFor(false, 'listening')).toBe('orbit');
    expect(glyphFor(null, 'thinking')).toBe('flame');
    expect(glyphFor(false, 'speaking')).toBe('play');
    expect(glyphFor(false, 'idle')).toBe('star');
    expect(glyphFor(null, null)).toBe('star');
  });

  it('ages format', () => {
    expect(fmtAge(2000)).toBe('2s');
    expect(fmtAge(125_000)).toBe('2m');
    expect(fmtAge(2 * 3600_000)).toBe('2h');
  });

  it('nav words are matched locally', () => {
    for (const w of ['deck', 'Deck.', 'open the deck', 'the deck', 'open deck']) expect(navWord(w)).toBe('deck');
    expect(navWord('core')).toBe('core');
    expect(navWord('deck the halls')).toBeNull();
    expect(navWord('lock the screen')).toBeNull();
  });
});

describe('strip lamps', () => {
  const online = { phase: 'online' as const, rttMs: 42.4, lastOk: 0 };

  it('shows real values and -- for unknown', () => {
    const lamps = stripLamps(online, laptopFromState(STATE, 0), { phase: 'idle' }, { callLive: false }, false, 0);
    const by = Object.fromEntries(lamps.map((l) => [l.key, l]));
    expect(by.LNK.value).toBe('42ms');
    expect(by.CALL.value).toBe('IDLE');
    expect(by.GST).toMatchObject({ value: 'ON', tone: 'guest' });
    expect(by.LCK.value).toBe('OPEN');
    expect(by.PWR).toMatchObject({ value: '87%', bolt: true });
    expect(by.PRS.value).toBe('DESK');
    expect(by.UPD).toBeUndefined();
    const empty = stripLamps(online, emptyLaptop(), { phase: 'idle' }, { callLive: false }, true, 0);
    const e = Object.fromEntries(empty.map((l) => [l.key, l]));
    expect([e.GST.value, e.LCK.value, e.PWR.value, e.PRS.value]).toEqual(['--', '--', '--', '--']);
    expect(e.UPD.value).toBe('NEW');
  });

  it('offline greys lamps and shows the last age', () => {
    const lap = laptopFromState(STATE, 0);
    const lamps = stripLamps({ phase: 'offline', rttMs: null, lastOk: 0 }, lap, { phase: 'live' }, { callLive: true }, false, 120_000);
    const by = Object.fromEntries(lamps.map((l) => [l.key, l]));
    expect(by.LNK).toMatchObject({ value: 'OFF', tone: 'fail' });
    expect(by.LCK).toMatchObject({ value: 'OPEN', tone: 'off', stale: '2m' });
  });

  it('call phases', () => {
    const lap = emptyLaptop();
    const v = (phase: Parameters<typeof stripLamps>[2]['phase'], live = false) =>
      stripLamps(online, lap, { phase }, { callLive: live }, false, 0).find((l) => l.key === 'CALL')!.value;
    expect(v('live')).toBe('LIVE');
    expect(v('reconnecting')).toBe('RCN');
    expect(v('idle', true)).toBe('LIVE');
  });
});

describe('api errors', () => {
  const fakeFetch = (status: number, body: unknown) =>
    (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it('classifies', () => {
    expect(classify(401, { auth: 'pair' })).toBe('auth');
    expect(classify(401, { error: 'gate', gate: 'soft' })).toBe('gate');
    expect(classify(403, { error: 'stepup' })).toBe('stepup');
    expect(classify(401, { error: 'denied' })).toBe('server');
    expect(classify(500, {})).toBe('server');
  });

  it('writes auth on gate errors and types them', async () => {
    await expect(api('GET', '/x', undefined, { fetchFn: fakeFetch(401, { error: 'gate', gate: 'soft' }) })).rejects.toMatchObject({
      kind: 'gate',
      status: 401,
    });
    expect(auth.value.gate).toBe('soft');
    await expect(api('GET', '/x', undefined, { fetchFn: fakeFetch(401, { auth: 'pair' }) })).rejects.toMatchObject({ kind: 'auth' });
    expect(auth.value.paired).toBe(false);
  });

  it('network failure and timeout are offline', async () => {
    const boom = (async () => {
      throw new TypeError('net');
    }) as unknown as typeof fetch;
    await expect(api('GET', '/x', undefined, { fetchFn: boom })).rejects.toBeInstanceOf(ApiError);
    await expect(api('GET', '/x', undefined, { fetchFn: boom })).rejects.toMatchObject({ kind: 'offline' });
    const hang = ((_: unknown, init: RequestInit) =>
      new Promise((_r, rej) => init.signal!.addEventListener('abort', () => rej(new Error('abort'))))) as unknown as typeof fetch;
    await expect(api('GET', '/x', undefined, { fetchFn: hang, timeoutMs: 5 })).rejects.toMatchObject({ kind: 'offline' });
  });

  it('sends the phone header and JSON body', async () => {
    let seen: RequestInit | undefined;
    let url = '';
    const f = (async (u: string, init: RequestInit) => {
      url = u;
      seen = init;
      return new Response('{"ok":true}');
    }) as unknown as typeof fetch;
    await api('POST', '/phone/v1/control', { action: 'lock' }, { fetchFn: f });
    expect(url).toBe('/api/phone/v1/control');
    expect((seen!.headers as Record<string, string>)['X-Jarvis-Phone']).toBe('1');
    expect(seen!.credentials).toBe('same-origin');
    expect(seen!.body).toBe('{"action":"lock"}');
  });
});

describe('poller', () => {
  function harness(results: (() => Promise<Record<string, unknown>>)[]) {
    const timers: { fn: () => void; ms: number }[] = [];
    let lost: string | null = null;
    let i = 0;
    const p = new Poller({
      get: () => results[Math.min(i++, results.length - 1)](),
      now: () => 1000,
      perf: () => 0,
      setTimer: (fn, ms) => (timers.push({ fn, ms }), timers.length),
      clearTimer: () => {},
      hidden: () => false,
      onGateLost: (k) => (lost = k),
    });
    return { p, timers, lost: () => lost };
  }

  it('backs off 2, 4, 8, 15 s while offline and resets on success', async () => {
    const fail = () => Promise.reject(new ApiError('offline', 0));
    const ok = () => Promise.resolve(STATE as unknown as Record<string, unknown>);
    const h = harness([fail, fail, fail, fail, fail, ok]);
    h.p.start();
    const delays: number[] = [];
    for (let n = 0; n < 6; n++) {
      await h.p.tick();
      delays.push(h.p.nextDelay());
    }
    expect(delays).toEqual([...BACKOFF_MS, 15000, 2000]);
    expect(link.value.phase).toBe('online');
    expect(laptop.value.guest.value).toBe(true);
  });

  it('stops and reports a lost gate', async () => {
    const h = harness([() => Promise.reject(new ApiError('gate', 401, { gate: 'soft' }))]);
    h.p.start();
    await h.p.tick();
    expect(h.lost()).toBe('gate');
    expect(h.p.active).toBe(false);
  });
});
