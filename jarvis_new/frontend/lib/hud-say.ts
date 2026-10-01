'use client';

import { useSyncExternalStore } from 'react';

/** Latest typed exchange, for the caption line.
 *
 *  The command field's SEND posts to bridge /chat (text side-channel —
 *  the webview has no LiveKit client, so typed text can't enter the voice
 *  room). The reply lands here and the live caption surfaces it for a few
 *  seconds. No React context: a tiny external store both components share.
 */

type Said = { text: string; at: number } | null;

let current: Said = null;
const listeners = new Set<() => void>();

export function sayText(text: string): void {
  current = { text, at: Date.now() };
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): Said {
  return current;
}

/** Latest said line, or null when older than `maxAgeMs`. */
export function useSaid(maxAgeMs = 12000): string | null {
  const said = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  if (!said) return null;
  return Date.now() - said.at <= maxAgeMs ? said.text : null;
}
