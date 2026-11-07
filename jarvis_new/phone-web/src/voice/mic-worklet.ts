/** AudioWorklet (runs in a 16 kHz AudioContext; Chrome resamples the mic):
 * Float32 → 512-sample PCM16 LE frames with the 0x01 kind byte, plus RMS. */
import { Framer, micFrame, rms } from './pcm';

declare class AudioWorkletProcessor {
  port: MessagePort;
}
declare function registerProcessor(name: string, ctor: unknown): void;

class MicProcessor extends AudioWorkletProcessor {
  private framer = new Framer();
  process(inputs: Float32Array[][]) {
    const ch = inputs[0]?.[0];
    if (ch && ch.length) {
      this.framer.push(ch, (frame) => {
        const bytes = micFrame(frame);
        this.port.postMessage({ frame: bytes.buffer, rms: rms(frame) }, [bytes.buffer]);
      });
    }
    return true;
  }
}

registerProcessor('link-mic', MicProcessor);
