'use client';

import { useSyncExternalStore } from 'react';
import { bridgeGet, bridgePost } from '@/lib/bridge';
import type { CharacterPublic, CharactersPayload, Motion, Phase, Snapshot } from './types';

export const DEFAULT_ID = 'jarvis';
const POLL_IDLE_MS = 1500;
const POLL_BUSY_MS = 250;
const LS_EFFECTS = 'jarvis.cx.effects';
const LS_REDUCED = 'jarvis.cx.reduced';

export type SwitchEvent = { to: string; from: string; turn: number; source: string };

export type CharacterState = {
  ready: boolean;
  /** Committed character: the skin the HUD wears when no transition runs. */
  active: string;
  /** Bridge's committed character (may lead or trail `active` mid-switch). */
  backend: string;
  target: string | null;
  phase: Phase;
  turn: number;
  order: string[];
  characters: Record<string, CharacterPublic>;
  motion: Motion | null;
  effects: boolean;
  reducedSetting: boolean;
  /** Last user-facing line (already-active, unknown name, rollback reason). */
  notice: string | null;
};

let state: CharacterState = {
  ready: false,
  active: DEFAULT_ID,
  backend: DEFAULT_ID,
  target: null,
  phase: 'idle',
  turn: 0,
  order: [DEFAULT_ID],
  characters: {},
  motion: null,
  effects: true,
  reducedSetting: false,
  notice: null,
};

const subs = new Set<() => void>();
const switchSubs = new Set<(e: SwitchEvent) => void>();
let timer: ReturnType<typeof setTimeout> | null = null;
let started = false;
let seenTurn = 0;
/** Character the visuals currently show; the engine reports back here. */
let visualId = DEFAULT_ID;

function set(patch: Partial<CharacterState>) {
  state = { ...state, ...patch };
  subs.forEach((f) => f());
}

function readLs(key: string, dflt: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? dflt : v === '1';
  } catch {
    return dflt;
  }
}

export function setEffects(on: boolean) {
  try {
    localStorage.setItem(LS_EFFECTS, on ? '1' : '0');
  } catch {
    /* private mode: setting is per session */
  }
  set({ effects: on });
}

export function setReducedSetting(on: boolean) {
  try {
    localStorage.setItem(LS_REDUCED, on ? '1' : '0');
  } catch {
    /* ignore */
  }
  set({ reducedSetting: on });
}

export function prefersReduced(): boolean {
  if (state.reducedSetting) return true;
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

export function setVisualId(id: string) {
  visualId = id;
}

export function onSwitch(f: (e: SwitchEvent) => void): () => void {
  switchSubs.add(f);
  return () => switchSubs.delete(f);
}

function emit(e: SwitchEvent, force = false) {
  if (!force) {
    if (e.turn <= seenTurn) return;
    seenTurn = e.turn;
  }
  switchSubs.forEach((f) => f(e));
}

let engineBusy = false;
let stray = 0;

/** Engine reports whether a transition is running (drives reconcile). */
export function setEngineBusy(b: boolean) {
  engineBusy = b;
}

function applySnapshot(s: Snapshot | undefined | null, source = 'voice') {
  if (!s) return;
  // `active` is the skin on screen; only the engine moves it. The bridge's
  // committed character is `backend` and only reconciles when the UI is idle.
  set({ target: s.target, phase: s.phase, turn: s.char_turn, backend: s.active });
  const to = s.target && s.phase !== 'rollback' ? s.target : s.active;
  if (s.char_turn > seenTurn && to !== visualId) {
    stray = 0;
    emit({ to, from: visualId, turn: s.char_turn, source });
    return;
  }
  if (s.char_turn > seenTurn) seenTurn = s.char_turn;
  // Rollback or a missed switch: bridge settled on a character the UI is not
  // showing. Animate to it (never leave a half-themed or wrong skin).
  if (s.phase === 'idle' && s.active !== visualId && !engineBusy) {
    if (++stray >= 2) {
      stray = 0;
      emit({ to: s.active, from: visualId, turn: s.char_turn, source: 'reconcile' }, true);
    }
  } else {
    stray = 0;
  }
}

async function poll() {
  const snap = await bridgeGet<Snapshot & { ok?: boolean }>('/character', 2500);
  if (snap && snap.active) applySnapshot(snap);
  const busy = state.phase !== 'idle';
  timer = setTimeout(() => void poll(), busy ? POLL_BUSY_MS : POLL_IDLE_MS);
}

export function startCharacterStore() {
  if (started) return;
  started = true;
  set({
    effects: readLs(LS_EFFECTS, true),
    reducedSetting: readLs(LS_REDUCED, false),
  });
  void (async () => {
    for (let i = 0; i < 20 && !state.ready; i++) {
      const p = await bridgeGet<CharactersPayload>('/characters', 4000);
      if (p && p.characters && p.motion) {
        const ids = Object.keys(p.characters);
        // Jarvis first, rest alphabetical: stable picker order.
        const order = [DEFAULT_ID, ...ids.filter((x) => x !== DEFAULT_ID).sort()].filter((x) =>
          ids.includes(x)
        );
        set({ ready: true, characters: p.characters, order, motion: p.motion });
        break;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    if (state.ready) void poll();
  })();
}

export function stopCharacterStore() {
  if (timer) clearTimeout(timer);
  timer = null;
  started = false;
}

type SwitchReply = {
  ok?: boolean;
  handled?: boolean;
  switched?: boolean;
  reply?: string;
  target?: string;
  char_turn?: number;
  character?: Snapshot;
};

/** UI trigger. Visuals start from the reply (same path as voice and text). */
export async function requestSwitch(target: string, source = 'ui'): Promise<void> {
  const r = await bridgePost<SwitchReply>('/character/switch', { target, source });
  if (!r) {
    set({ notice: 'Bridge unreachable.' });
    return;
  }
  if (r.switched === false || r.handled === false) {
    set({ notice: r.reply ?? null });
    return;
  }
  const turn = r.char_turn ?? r.character?.char_turn;
  if (typeof turn === 'number') {
    set({ notice: null });
    emit({ to: target, from: visualId, turn, source });
    if (r.character) set({ target: r.character.target, phase: r.character.phase, turn });
  }
}

/** Engine tells the store the transition landed so `active` follows. */
export function commitVisual(id: string) {
  visualId = id;
  set({ active: id });
}

export function useCharacters(): CharacterState {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => state,
    () => state
  );
}

export function getCharacterState(): CharacterState {
  return state;
}

/** A switch the bridge already accepted (typed chat reply). Visuals follow. */
export function noteSwitch(target: string, turn: number, source = 'text') {
  emit({ to: target, from: visualId, turn, source });
}
