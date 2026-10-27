'use client';

import { useCharacters } from './store';
import type { CharacterPublic } from './types';

export type Skin = {
  id: string;
  /** null for Jarvis: callers keep the stock values untouched. */
  ch: CharacterPublic | null;
};

export function useSkin(): Skin {
  const st = useCharacters();
  const ch = st.active !== 'jarvis' ? (st.characters[st.active] ?? null) : null;
  return { id: ch ? st.active : 'jarvis', ch };
}

/** J.A.R.V.I.S. -> U.L.T.R.O.N. */
export function wordmarkOf(ch: CharacterPublic): string {
  return `${[...ch.display_name.replace(/[^A-Za-z]/g, '').toUpperCase()].join('.')}.`;
}

export function taglineOf(ch: CharacterPublic): string {
  return ch.lines.tagline?.[0] ?? '';
}

/** Orb tint per state for a non-Jarvis skin (palette tokens, no new colors). */
export function skinStateColor(ch: CharacterPublic, jarvis: string): string {
  const t = ch.theme_tokens;
  const pick = (k: string) => String(t[k] ?? t.accent);
  switch (jarvis) {
    case 'listening':
      return pick('accent_hot');
    case 'thinking':
      return pick('warn');
    default:
      return pick('accent');
  }
}
