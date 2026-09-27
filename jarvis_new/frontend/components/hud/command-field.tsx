'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useMicMuted } from '@/hooks/hud/use-jarvis-state';
import { bridgeChat, bridgeSummon } from '@/lib/bridge';
import { sayText } from '@/lib/hud-say';
import { hideOverlay, isTauri, summonTalk } from '@/lib/tauri';

type Props = {
  supportsChatInput?: boolean;
  ghostHint?: string;
};

/**
 * Typed command field + PTT key. SEND posts to bridge /chat (text
 * side-channel — the webview has no LiveKit client, so typed text can't
 * enter the voice room; the reply surfaces on the caption line). The MIC
 * button still mutes/unmutes the native hotword + in-call pump via the
 * shell. NumpadEnter is push-to-talk: it summons a voice call through
 * the shell talk chain (same summon as "hey Jarvis", no hotword needed);
 * `/` focuses this field for typing instead.
 */
export function CommandField({ supportsChatInput = true, ghostHint = '' }: Props) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [talking, setTalking] = useState(false);
  const talkingRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // Shell mic mute (hotword + in-call pump). Outside Tauri this stays
  // unknown and the button is inert (see useMicMuted).
  const { muted, toggle: toggleMute } = useMicMuted();

  // PTT summon: NumpadEnter asks the wake listener for a talk session.
  // Re-entrancy guarded by ref (StrictMode-safe); the wake listener also
  // refuses layered second calls, and the room watcher mirrors progress.
  const ptt = useCallback(async () => {
    if (talkingRef.current) return;
    talkingRef.current = true;
    setTalking(true);
    try {
      const ok = isTauri() ? (await summonTalk()).ok === true : await bridgeSummon();
      if (!ok) sayText('(voice summon unreachable — bridge offline?)');
    } catch {
      sayText('(voice summon unreachable — bridge offline?)');
    } finally {
      talkingRef.current = false;
      setTalking(false);
    }
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // NumpadEnter = push-to-talk (key repeat ignored).
      if (e.code === 'NumpadEnter' && !e.repeat) {
        e.preventDefault();
        void ptt();
      }
      // Esc hides the overlay in the shell (blur-hide covers the rest);
      // plain-browser dev just drops field focus.
      if (e.key === 'Escape') {
        e.preventDefault();
        void hideOverlay();
        inputRef.current?.blur();
      }
    };
    // Autofocus on show: the shell focuses the overlay window on every
    // summon (Super+J / tray Talk), which fires a window focus event in
    // the webview. (No @tauri-apps/api dep by design — see lib/tauri.ts —
    // so we listen for focus instead of the Rust `jarvis-toggle` event.)
    const onFocus = () => {
      inputRef.current?.focus();
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('focus', onFocus);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('focus', onFocus);
    };
  }, [ptt]);

  const submit = async () => {
    const text = value.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      const reply = await bridgeChat(text);
      sayText(reply ?? '(no answer — bridge unreachable)');
      setValue('');
    } catch {
      /* keep text on failure */
    } finally {
      setBusy(false);
    }
  };

  if (!supportsChatInput) return null;

  return (
    <form
      className="hud-command"
      data-talking={talking ? 'true' : 'false'}
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <span className="hud-command__prompt" aria-hidden="true">
        ›
      </span>
      <input
        id="jarvis-command"
        name="jarvis-command"
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={ghostHint || 'NumpadEnter to talk — / to type…'}
        aria-label="Command input. Press slash to focus from anywhere."
        aria-keyshortcuts="/"
        className="hud-command__input"
        autoComplete="off"
        spellCheck={false}
      />
      {ghostHint && !value ? <span className="hud-command__ghost">{ghostHint}</span> : null}
      <button
        type="button"
        onClick={() => void toggleMute()}
        aria-pressed={muted === true}
        aria-label={muted === true ? 'Unmute microphone' : 'Mute microphone'}
        title={
          muted === true ? 'Mic muted — hotword + call silent' : 'Mute mic — hotword + call silent'
        }
        className="hud-command__mic"
        data-muted={muted === true ? 'true' : 'false'}
      >
        {muted === true ? 'MUTED' : 'MIC'}
      </button>
      <button
        type="submit"
        className="hud-command__send"
        disabled={busy || !value.trim()}
        aria-label={busy ? 'Sending command…' : 'Send command'}
      >
        {busy ? '…' : 'SEND'}
      </button>
    </form>
  );
}
