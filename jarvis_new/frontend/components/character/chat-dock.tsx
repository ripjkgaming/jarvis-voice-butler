'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { bridgeCaptions, bridgeChatFull } from '@/lib/bridge';
import { getCharacterState, noteSwitch, useCharacters } from '@/lib/character/store';
import type { CharacterPublic } from '@/lib/character/types';
import s from './character.module.css';

type Msg = {
  id: string;
  who: 'user' | 'char';
  /** Character id that said it (or was active when the user typed it). */
  speaker: string;
  text: string;
  ts: number;
};

/** Chat history is shared across characters and kept for the whole run. */
let history: Msg[] = [];
const listeners = new Set<() => void>();
const push = (m: Msg) => {
  history = [...history, m].slice(-200);
  listeners.forEach((f) => f());
};

function bubbleStyle(ch: CharacterPublic | undefined, who: 'user' | 'char') {
  const t = ch?.theme_tokens;
  if (!t) return {};
  return {
    background: String(who === 'user' ? t.bubble_user : t.bubble_char),
    borderColor: String(t.line_strong),
    color: String(t.text),
  };
}

async function hydrate() {
  const caps = (await bridgeCaptions(30)) ?? [];
  if (!caps.length) return;
  const seen = new Set(history.map((m) => `${m.who}|${m.text}`));
  const add = caps
    .map(
      (c): Msg => ({
        id: `cap-${c.ts}-${c.text.length}`,
        who: c.role === 'sir' ? 'user' : 'char',
        speaker: c.speaker || 'jarvis',
        text: c.text,
        ts: c.ts * 1000,
      })
    )
    .filter((m) => !seen.has(`${m.who}|${m.text}`));
  if (!add.length) return;
  history = [...add, ...history].sort((a, b) => a.ts - b.ts).slice(-200);
  listeners.forEach((f) => f());
}

export function ChatDock() {
  const st = useCharacters();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [, force] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const f = () => force((n) => n + 1);
    listeners.add(f);
    return () => {
      listeners.delete(f);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    void hydrate();
    const t = setInterval(() => void hydrate(), 3000);
    return () => clearInterval(t);
  }, [open]);

  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history.length, open, busy]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing =
        !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        setOpen(true);
        setTimeout(() => input.current?.focus(), 0);
      } else if (e.key === 'Escape' && open && typing && t === input.current) {
        e.stopPropagation();
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open]);

  const send = useCallback(async () => {
    const msg = text.trim();
    if (!msg || busy) return;
    const before = getCharacterState().active;
    setText('');
    push({ id: `u-${Date.now()}`, who: 'user', speaker: before, text: msg, ts: Date.now() });
    setBusy(true);
    const turns: [string, string, string?][] = history
      .slice(-20)
      .map((m) => [m.who === 'user' ? 'user' : 'jarvis', m.text, m.speaker]);
    const r = await bridgeChatFull(msg, turns.slice(0, -1));
    setBusy(false);
    if (!r) {
      push({
        id: `e-${Date.now()}`,
        who: 'char',
        speaker: before,
        text: '(bridge unreachable)',
        ts: Date.now(),
      });
      return;
    }
    const answered = r.switched && r.target ? r.target : (r.speaker ?? getCharacterState().active);
    if (r.switched && r.target && typeof r.char_turn === 'number') {
      noteSwitch(r.target, r.char_turn, 'text');
    }
    push({
      id: `c-${Date.now()}`,
      who: 'char',
      speaker: answered,
      text: r.reply ?? (r.warning ? `(${r.warning})` : '(no answer)'),
      ts: Date.now(),
    });
  }, [text, busy]);

  if (!st.ready) return null;
  const cur = st.characters[st.active];

  if (!open) {
    return (
      <div className={s.dock} data-open="false" data-tauri-drag-region="false">
        <button
          type="button"
          className={s.pill}
          onClick={() => setOpen(true)}
          aria-label="Open chat"
        >
          <span aria-hidden="true">›</span> CHAT <span className={s.kbd}>/</span>
        </button>
      </div>
    );
  }

  return (
    <div className={s.dock} data-open="true" role="dialog" aria-label="Chat">
      <div className={s.head}>
        <span>SECURE CHANNEL · {history.length}</span>
        <button
          type="button"
          className={s.close}
          onClick={() => setOpen(false)}
          aria-label="Close chat"
        >
          ✕
        </button>
      </div>
      <div className={s.log} ref={log} aria-live="polite">
        {history.length === 0 ? <span className={s.empty}>NO MESSAGES YET</span> : null}
        {history.map((m) => {
          const ch = st.characters[m.speaker];
          return (
            <div className={s.msg} data-who={m.who} key={m.id}>
              <span
                className={s.tag}
                style={{ color: String(ch?.theme_tokens.accent ?? 'inherit') }}
              >
                {m.who === 'user'
                  ? `YOU · ${(ch?.display_name ?? '').toUpperCase()}`
                  : (ch?.display_name ?? m.speaker).toUpperCase()}
              </span>
              <div className={s.bubble} style={bubbleStyle(ch, m.who)}>
                {m.text}
              </div>
            </div>
          );
        })}
        {busy ? (
          <div className={s.msg} data-who="char">
            <span className={s.tag}>{(cur?.display_name ?? '').toUpperCase()}</span>
            <div className={s.bubble} style={bubbleStyle(cur, 'char')}>
              …
            </div>
          </div>
        ) : null}
      </div>
      <form
        className={s.form}
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          ref={input}
          className={s.input}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={`Message ${cur?.display_name ?? ''} — or “switch to Ultron”`}
          aria-label="Chat message"
          autoComplete="off"
          spellCheck={false}
          autoFocus
        />
        <button type="submit" className={s.send} disabled={busy || !text.trim()}>
          SEND
        </button>
      </form>
    </div>
  );
}
