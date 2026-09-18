'use client';

import { useEffect, useRef, useState } from 'react';
import { useChat } from '@livekit/components-react';
import { useMicMuted } from '@/hooks/hud/use-jarvis-state';
import { hideOverlay } from '@/lib/tauri';

type Props = {
  supportsChatInput?: boolean;
  ghostHint?: string;
};

/**
 * Typed command field. Sends via the existing chat-input path
 * (session.sendText). Voice path untouched; both hit the same agent.
 * NumpadEnter focuses the HUD window + field (see KDE doc in HUD_PLAN).
 */
export function CommandField({ supportsChatInput = true, ghostHint = '' }: Props) {
  const { send } = useChat();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // Shell mic mute (hotword + in-call pump). Outside Tauri this stays
  // unknown and the button is inert (see useMicMuted).
  const { muted, toggle: toggleMute } = useMicMuted();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // NumpadEnter globally focuses the command field.
      if (e.code === 'NumpadEnter') {
        e.preventDefault();
        window.focus();
        inputRef.current?.focus();
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
  }, []);

  const submit = async () => {
    const text = value.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      await send(text);
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
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <span className="hud-command__prompt">›</span>
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={ghostHint || 'Type a command — Numpad Enter focuses…'}
        aria-label="Command input"
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
      <button type="submit" className="hud-command__send" disabled={busy || !value.trim()}>
        SEND
      </button>
    </form>
  );
}
