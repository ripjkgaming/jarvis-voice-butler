import type { BridgeCaptions, BridgeLiveCaption } from './bridge';

export type VoiceCaption = {
  role: 'sir' | 'jarvis';
  text: string;
  key: string;
  live: boolean;
};

/** Pick the most recent spoken line across the log and synced audio.
 * The legacy log rounds timestamps to seconds, while live captions retain
 * fractions. A user line in the same second must win that ambiguous tie;
 * otherwise a finished greeting can hide it until another turn arrives. */
export function voiceCaptionFrom(
  captions: NonNullable<BridgeCaptions['captions']>,
  live: BridgeLiveCaption | null,
  now = Date.now() / 1000,
  maxAgeS = 120
): VoiceCaption | null {
  const tail = captions.at(-1);
  const last = tail && now - tail.ts < maxAgeS ? tail : null;
  const freshLive = live?.text.trim() && now - live.ts < maxAgeS ? live : null;
  // Compare at the log's precision. New writers can retain fractions
  // without changing this reader or losing their ordering information.
  const liveAt =
    freshLive && last && Number.isInteger(last.ts) ? Math.floor(freshLive.ts) : freshLive?.ts;
  const showLive =
    freshLive &&
    (!last ||
      (liveAt !== undefined && (last.role === 'sir' ? liveAt > last.ts : liveAt >= last.ts)));
  if (showLive) {
    return { role: 'jarvis', text: freshLive.text, key: `l${freshLive.id}`, live: true };
  }
  return last
    ? {
        role: last.role === 'sir' ? 'sir' : 'jarvis',
        text: last.text,
        key: `c${last.ts}`,
        live: false,
      }
    : null;
}
