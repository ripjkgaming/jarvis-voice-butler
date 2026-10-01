'use client';

/** Live wake room targeted by the HUD's receive-only session.
 *
 *  Plain module singleton (no React): the room watcher
 *  (ViewController) writes it, the LiveKit TokenSource callback in
 *  `app.tsx` reads it at fetch time. Null = no call right now.
 */

let target: string | null = null;

export function setHudRoom(room: string | null): void {
  target = room;
}

export function getHudRoom(): string | null {
  return target;
}
