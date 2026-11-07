/** Pure PCM helpers shared by the worklets and tests (docs/voice-transport.md).
 * Mic: Float32 at 16 kHz → 512-sample frames → [0x01][1024 B PCM16 LE].
 * Playback: PCM16 LE 24 kHz chunks into a Float32 ring buffer that starts at
 * 120 ms buffered and never holds more than 1 s (oldest dropped). */

export const KIND_MIC = 0x01;
export const KIND_AUDIO = 0x02;
export const MIC_RATE = 16000;
export const PLAY_RATE = 24000;
export const FRAME_SAMPLES = 512;
export const FRAME_BYTES = 1 + FRAME_SAMPLES * 2;

/** One mic frame: kind byte + 512 clamped PCM16 little-endian samples. */
export function micFrame(samples: Float32Array): Uint8Array {
  if (samples.length !== FRAME_SAMPLES) throw new Error('frame must be 512 samples');
  const out = new Uint8Array(FRAME_BYTES);
  out[0] = KIND_MIC;
  const view = new DataView(out.buffer);
  for (let i = 0; i < FRAME_SAMPLES; i++) {
    const s = samples[i] < -1 ? -1 : samples[i] > 1 ? 1 : samples[i] || 0;
    view.setInt16(1 + i * 2, s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff), true);
  }
  return out;
}

export function rms(samples: Float32Array, n = samples.length): number {
  let sum = 0;
  for (let i = 0; i < n; i++) sum += samples[i] * samples[i];
  return n ? Math.sqrt(sum / n) : 0;
}

/** Accumulates arbitrary-size input blocks into exact 512-sample frames. */
export class Framer {
  private buf = new Float32Array(FRAME_SAMPLES);
  private n = 0;
  push(input: Float32Array, emit: (frame: Float32Array) => void) {
    let i = 0;
    while (i < input.length) {
      const take = Math.min(FRAME_SAMPLES - this.n, input.length - i);
      this.buf.set(input.subarray(i, i + take), this.n);
      this.n += take;
      i += take;
      if (this.n === FRAME_SAMPLES) {
        emit(this.buf.slice());
        this.n = 0;
      }
    }
  }
}

export function pcm16ToFloat(bytes: Uint8Array): Float32Array {
  const n = bytes.length >> 1;
  const view = new DataView(bytes.buffer, bytes.byteOffset, n * 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 0x8000;
  return out;
}

export class Ring {
  readonly cap: number;
  readonly start: number;
  private data: Float32Array;
  private r = 0;
  private w = 0;
  size = 0;
  playing = false;
  dropped = 0;

  constructor(rate = PLAY_RATE, startMs = 120, maxMs = 1000) {
    this.cap = Math.round((rate * maxMs) / 1000);
    this.start = Math.round((rate * startMs) / 1000);
    this.data = new Float32Array(this.cap);
  }

  write(samples: Float32Array) {
    for (let i = 0; i < samples.length; i++) {
      if (this.size === this.cap) {
        this.r = (this.r + 1) % this.cap; // drop oldest
        this.size--;
        this.dropped++;
      }
      this.data[this.w] = samples[i];
      this.w = (this.w + 1) % this.cap;
      this.size++;
    }
    if (!this.playing && this.size >= this.start) this.playing = true;
  }

  /** Fill `out`; silence while buffering. Returns samples actually played. */
  read(out: Float32Array): number {
    if (!this.playing) {
      out.fill(0);
      return 0;
    }
    let n = 0;
    for (; n < out.length && this.size > 0; n++) {
      out[n] = this.data[this.r];
      this.r = (this.r + 1) % this.cap;
      this.size--;
    }
    out.fill(0, n);
    if (this.size === 0) this.playing = false; // underrun: re-buffer to 120 ms
    return n;
  }

  flush() {
    this.r = this.w = this.size = 0;
    this.playing = false;
  }
}
