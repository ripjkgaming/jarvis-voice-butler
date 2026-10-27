'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { requestSwitch, useCharacters } from '@/lib/character/store';
import s from './character.module.css';

type Props = {
  /** Open the picker upward (chat dock sits at the bottom). */
  up?: boolean;
  className?: string;
};

function swatch(tokens: Record<string, string | number>): string {
  return `linear-gradient(135deg, ${tokens.accent_hot ?? tokens.accent}, ${tokens.accent})`;
}

/** Character chip + picker. Selecting runs the same switch path as voice and
 *  typed commands (POST /character/switch -> shared state machine). */
export function CharacterChip({ up = false, className }: Props) {
  const st = useCharacters();
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const wrap = useRef<HTMLSpanElement>(null);
  const busy = st.phase !== 'idle';

  const close = useCallback(() => setOpen(false), []);

  // The picker lives in the top bar's stacking context: lift it above the grid.
  useEffect(() => {
    const host = wrap.current?.closest<HTMLElement>('header');
    if (!open || !host) return;
    host.style.zIndex = '50';
    return () => {
      host.style.zIndex = '';
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close();
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const n = st.order.length;
        setFocus((f) => (f + (e.key === 'ArrowDown' ? 1 : n - 1)) % n);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const id = st.order[focus];
        if (id) void pick(id);
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, focus, st.order]);

  async function pick(id: string) {
    close();
    await requestSwitch(id, 'ui');
  }

  if (!st.ready) return null;
  const cur = st.characters[st.active];
  if (!cur) return null;

  return (
    <span className={`${s.chipWrap} ${className ?? ''}`} ref={wrap}>
      <button
        type="button"
        className={s.chip}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Character: ${cur.display_name}. Change character`}
        data-tauri-drag-region="false"
        onClick={() => {
          setFocus(Math.max(0, st.order.indexOf(st.active)));
          setOpen((o) => !o);
        }}
      >
        <i className={s.swatch} style={{ background: swatch(cur.theme_tokens) }} aria-hidden="true" />
        {cur.display_name}
        <span className={s.caret} aria-hidden="true">
          {busy ? '…' : '▾'}
        </span>
      </button>
      {open ? (
        <ul className={`${s.picker} ${up ? s.pickerUp : ''}`} role="listbox" aria-label="Characters">
          {st.order.map((id, i) => {
            const c = st.characters[id];
            if (!c) return null;
            return (
              <li key={id} role="none">
                <button
                  type="button"
                  role="option"
                  aria-selected={id === st.active}
                  className={s.row}
                  data-focus={i === focus}
                  onMouseEnter={() => setFocus(i)}
                  onClick={() => void pick(id)}
                >
                  <i className={s.swatch} style={{ background: swatch(c.theme_tokens), width: 14, height: 14 }} />
                  <span>
                    <span className={s.rowName}>{c.display_name}</span>
                    <span className={s.rowSub}>Calls you “{c.address}”</span>
                  </span>
                  {id === st.active ? <span className={s.rowTag}>ACTIVE</span> : null}
                </button>
              </li>
            );
          })}
          {st.notice ? <li className={s.notice}>{st.notice}</li> : null}
        </ul>
      ) : null}
    </span>
  );
}
