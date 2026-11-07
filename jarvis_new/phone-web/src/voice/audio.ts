/** Browser audio for a call (PLAN.md §8): mic → 16 kHz worklet → 0x01 frames;
 * 0x02 PCM16 24 kHz → playback worklet. Both contexts close on teardown. */
import micUrl from './mic-worklet.ts?worker&url';
import playUrl from './play-worklet.ts?worker&url';
import type { AudioIO } from './call';

export async function openAudio(): Promise<AudioIO> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const micCtx = new AudioContext({ sampleRate: 16000 });
  const playCtx = new AudioContext({ sampleRate: 24000, latencyHint: 'interactive' });
  try {
    await Promise.all([micCtx.audioWorklet.addModule(micUrl), playCtx.audioWorklet.addModule(playUrl)]);
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop());
    void micCtx.close();
    void playCtx.close();
    throw e;
  }
  const src = micCtx.createMediaStreamSource(stream);
  const mic = new AudioWorkletNode(micCtx, 'link-mic', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
  // Keep the mic node pulled by the graph without making any sound.
  const sink = micCtx.createGain();
  sink.gain.value = 0;
  src.connect(mic).connect(sink).connect(micCtx.destination);
  const play = new AudioWorkletNode(playCtx, 'link-play', { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1] });
  play.connect(playCtx.destination);
  await Promise.all([micCtx.resume(), playCtx.resume()]);
  let closed = false;
  return {
    onFrame(cb) {
      mic.port.onmessage = (e: MessageEvent<{ frame: ArrayBuffer; rms: number }>) => cb(e.data.frame, e.data.rms);
    },
    onPlayRms(cb) {
      play.port.onmessage = (e: MessageEvent<{ rms: number }>) => cb(e.data.rms);
    },
    play(pcm) {
      const copy = pcm.slice().buffer;
      play.port.postMessage(copy, [copy]);
    },
    flush() {
      play.port.postMessage('flush');
    },
    close() {
      if (closed) return;
      closed = true;
      stream.getTracks().forEach((t) => t.stop());
      mic.port.onmessage = null;
      play.port.onmessage = null;
      void micCtx.close();
      void playCtx.close();
    },
  };
}
