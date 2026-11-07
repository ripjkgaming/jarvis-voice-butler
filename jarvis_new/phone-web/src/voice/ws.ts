/** WebSocket framing (docs/voice-transport.md): binary [kind][payload],
 * text {"topic", "payload"}. */
import { KIND_AUDIO } from './pcm';

export type Incoming =
  | { type: 'audio'; pcm: Uint8Array }
  | { type: 'binary'; kind: number }
  | { type: 'topic'; topic: string; payload: unknown }
  | { type: 'bad' };

export const MAX_TEXT = 64 * 1024;

export function parse(data: ArrayBuffer | string): Incoming {
  if (typeof data === 'string') {
    if (data.length > MAX_TEXT) return { type: 'bad' };
    try {
      const msg = JSON.parse(data) as { topic?: unknown; payload?: unknown };
      if (typeof msg.topic !== 'string') return { type: 'bad' };
      return { type: 'topic', topic: msg.topic, payload: msg.payload };
    } catch {
      return { type: 'bad' };
    }
  }
  const u8 = new Uint8Array(data);
  if (!u8.length) return { type: 'bad' };
  if (u8[0] === KIND_AUDIO) return { type: 'audio', pcm: u8.subarray(1) };
  return { type: 'binary', kind: u8[0] };
}

export const topic = (name: string, payload: unknown) => JSON.stringify({ topic: name, payload });

export function wsUrl(loc: { protocol: string; host: string }, token: string) {
  const scheme = loc.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${loc.host}/voice?token=${encodeURIComponent(token)}`;
}
