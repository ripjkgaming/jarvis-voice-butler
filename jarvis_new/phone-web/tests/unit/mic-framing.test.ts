import { describe, expect, it } from 'vitest';
import { FRAME_BYTES, Framer, micFrame, pcm16ToFloat, Ring } from '../../src/voice/pcm';
import { parse, topic, wsUrl } from '../../src/voice/ws';

describe('mic framing', () => {
  it('a frame is exactly 1025 bytes: kind 0x01 + 512 PCM16 LE samples', () => {
    const s = new Float32Array(512);
    s[0] = 0.5;
    s[1] = -0.5;
    s[2] = 2; // clamped
    s[3] = -2;
    const f = micFrame(s);
    expect(f.length).toBe(1025);
    expect(FRAME_BYTES).toBe(1025);
    expect(f[0]).toBe(0x01);
    const v = new DataView(f.buffer);
    expect(v.getInt16(1, true)).toBe(16384);
    expect(v.getInt16(3, true)).toBe(-16384);
    expect(v.getInt16(5, true)).toBe(32767);
    expect(v.getInt16(7, true)).toBe(-32768);
    // little endian byte order
    expect([f[1], f[2]]).toEqual([0x00, 0x40]);
  });

  it('NaN is silence and wrong sizes are rejected', () => {
    const s = new Float32Array(512).fill(NaN);
    expect(new DataView(micFrame(s).buffer).getInt16(1, true)).toBe(0);
    expect(() => micFrame(new Float32Array(128))).toThrow();
  });

  it('framer emits exact 512-sample frames from 128-sample render quanta', () => {
    const fr = new Framer();
    const frames: Float32Array[] = [];
    for (let q = 0; q < 9; q++) fr.push(new Float32Array(128).fill(q), (f) => frames.push(f));
    expect(frames).toHaveLength(2);
    expect(frames[0][0]).toBe(0);
    expect(frames[0][511]).toBe(3);
    expect(frames[1][0]).toBe(4);
  });
});

describe('playback ring', () => {
  it('starts at 120 ms buffered, caps at 1 s, flushes', () => {
    const r = new Ring(24000, 120, 1000);
    const out = new Float32Array(128);
    r.write(new Float32Array(2000).fill(0.1));
    expect(r.read(out)).toBe(0); // still buffering (< 2880)
    r.write(new Float32Array(1000).fill(0.1));
    expect(r.read(out)).toBe(128);
    r.write(new Float32Array(30000).fill(0.2));
    expect(r.size).toBe(24000);
    expect(r.dropped).toBeGreaterThan(0);
    r.flush();
    expect(r.size).toBe(0);
    expect(r.read(out)).toBe(0);
  });

  it('pcm16 decode round-trips', () => {
    const f = micFrame(new Float32Array(512).fill(0.25));
    const back = pcm16ToFloat(f.subarray(1));
    expect(back[0]).toBeCloseTo(0.25, 3);
  });
});

describe('ws framing', () => {
  it('parses audio, topics and garbage', () => {
    const buf = new Uint8Array([0x02, 1, 2, 3, 4]).buffer;
    const a = parse(buf);
    expect(a.type).toBe('audio');
    expect(parse(topic('caption', { text: 'hi', final: true }))).toEqual({
      type: 'topic',
      topic: 'caption',
      payload: { text: 'hi', final: true },
    });
    expect(parse('nope').type).toBe('bad');
    expect(parse('{"payload":1}').type).toBe('bad');
    expect(parse(new ArrayBuffer(0)).type).toBe('bad');
  });

  it('dials the same origin over wss', () => {
    expect(wsUrl({ protocol: 'https:', host: 'ripjk.tailea4e7f.ts.net' }, 'a.b')).toBe('wss://ripjk.tailea4e7f.ts.net/voice?token=a.b');
    expect(wsUrl({ protocol: 'http:', host: 'localhost:4173' }, 't')).toBe('ws://localhost:4173/voice?token=t');
  });
});
