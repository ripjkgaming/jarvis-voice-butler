/** AudioWorklet (24 kHz AudioContext): PCM16 LE ring buffer, 120 ms start
 * target, 1 s cap, `flush` command, posts RMS about every 50 ms. */
import { pcm16ToFloat, Ring, rms } from './pcm';

declare class AudioWorkletProcessor {
  port: MessagePort;
}
declare function registerProcessor(name: string, ctor: unknown): void;

class PlayProcessor extends AudioWorkletProcessor {
  private ring = new Ring();
  private acc = 0;
  private sum = 0;
  private count = 0;
  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent) => {
      if (e.data === 'flush') this.ring.flush();
      else if (e.data instanceof ArrayBuffer) this.ring.write(pcm16ToFloat(new Uint8Array(e.data)));
    };
  }
  process(_inputs: Float32Array[][], outputs: Float32Array[][]) {
    const out = outputs[0]?.[0];
    if (!out) return true;
    const played = this.ring.read(out);
    const r = rms(out);
    this.sum += r * r * out.length;
    this.count += out.length;
    this.acc += out.length;
    if (this.acc >= 1200) {
      this.port.postMessage({ rms: Math.sqrt(this.sum / Math.max(1, this.count)), playing: played > 0 });
      this.acc = this.sum = this.count = 0;
    }
    return true;
  }
}

registerProcessor('link-play', PlayProcessor);
