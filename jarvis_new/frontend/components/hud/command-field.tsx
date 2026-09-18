'use client';

import { useEffect, useRef, useState } from 'react';
import { useChat } from '@livekit/components-react';

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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // NumpadEnter globally focuses the command field.
      if (e.code === 'NumpadEnter') {
        e.preventDefault();
        window.focus();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
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
      <button type="submit" className="hud-command__send" disabled={busy || !value.trim()}>
        SEND
      </button>
    </form>
  );
}
