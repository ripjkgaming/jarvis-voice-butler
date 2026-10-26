'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { bridgePost } from '@/lib/bridge';
import type { ProtocolPhase, ProtocolState } from '@/lib/protocol-layout';
import { useSharedPoll } from '@/lib/shared-poll';

/**
 * Protocol 12 lifecycle over the bridge's /protocol/state (src/netscan.py).
 *
 * - A run the HUD has not seen yet (its `run` counter moved past the value
 *   at mount, or one already discovering/scanning at mount) takes over the
 *   screen from any HUD state, without confirmation.
 * - 'aborted' (voice "stand down") restores the HUD; 'error' first holds
 *   the message on screen for ERROR_HOLD_MS. Esc aborts and restores now.
 * - 'done' keeps the map up until one of those.
 *
 * `view.exiting` starts the reverse fade; the layer calls `finish()` when
 * it has faded out, which drops the view.
 */
const POLL_MS = 400;
const ERROR_HOLD_MS = 1500;
const NOTICE_MS = 4000;
const LIVE: ReadonlySet<ProtocolPhase> = new Set(['discovering', 'scanning']);

export type ProtocolExit = 'esc' | 'aborted' | 'error' | 'suspended';
export type ProtocolView = { run: number; exiting: ProtocolExit | null };
export type ProtocolToast = { seq: number; text: string };

export function useProtocol(enabled: boolean) {
  const state = useSharedPoll<ProtocolState>('/protocol/state', POLL_MS);
  const [view, setView] = useState<ProtocolView | null>(null);
  const baseline = useRef<number | null>(null);
  const dismissed = useRef<number | null>(null);

  useEffect(() => {
    if (!state || typeof state.run !== 'number') return;
    const { run, phase } = state;
    // A run already live when the HUD mounted (reload mid-scan) is fresh.
    if (baseline.current === null) baseline.current = LIVE.has(phase) ? run - 1 : run;
    const fresh = run > baseline.current && run !== dismissed.current;
    if (!view || view.run !== run) {
      if (enabled && fresh && (LIVE.has(phase) || phase === 'done')) {
        setView({ run, exiting: null });
      }
      return;
    }
    if (view.exiting) return;
    if (phase === 'aborted' || phase === 'idle') {
      dismissed.current = run;
      setView({ run, exiting: 'aborted' });
    }
  }, [state, enabled, view]);

  const phase = state?.phase;
  const live = !!view && !view.exiting;

  // 'error': hold the message in the terminal, then restore.
  useEffect(() => {
    if (!live || !view || phase !== 'error') return;
    const run = view.run;
    const t = setTimeout(() => {
      dismissed.current = run;
      setView((v) => (v && v.run === run && !v.exiting ? { run, exiting: 'error' } : v));
    }, ERROR_HOLD_MS);
    return () => clearTimeout(t);
  }, [live, view, phase]);

  // The HUD face went away under us (school mode, a shell transition).
  useEffect(() => {
    if (enabled || !view || view.exiting) return;
    dismissed.current = view.run;
    setView({ run: view.run, exiting: 'suspended' });
  }, [enabled, view]);

  // Esc beats hud-shell's window Escape (hideOverlay): capture phase on
  // window runs before its bubble listener, and we stop it there.
  useEffect(() => {
    if (!live || !view) return;
    const run = view.run;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      dismissed.current = run;
      void bridgePost('/protocol/abort', {});
      setView({ run, exiting: 'esc' });
    };
    window.addEventListener('keydown', onKey, { capture: true });
    return () => window.removeEventListener('keydown', onKey, { capture: true });
  }, [live, view]);

  const finish = useCallback((run: number) => {
    setView((v) => (v && v.run === run && v.exiting ? null : v));
  }, []);

  // HUD toast: only notices whose seq moves past the one seen at mount.
  const [toast, setToast] = useState<ProtocolToast | null>(null);
  const lastSeq = useRef<number | null>(null);
  const notice = state?.notice;
  useEffect(() => {
    if (!state) return;
    const seq = typeof notice?.seq === 'number' ? notice.seq : null;
    if (lastSeq.current === null) {
      lastSeq.current = seq ?? 0;
      return;
    }
    if (seq === null || seq <= lastSeq.current) return;
    lastSeq.current = seq;
    const text = typeof notice?.text === 'string' ? notice.text.trim() : '';
    if (text) setToast({ seq, text });
  }, [state, notice?.seq, notice?.text]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast((x) => (x === toast ? null : x)), NOTICE_MS);
    return () => clearTimeout(t);
  }, [toast]);

  const pre = state?.preflight;
  const preflightMessage =
    pre && pre.nmap === false && typeof pre.message === 'string' && pre.message.trim()
      ? pre.message.trim()
      : null;

  return { state, view, finish, toast, preflightMessage };
}
