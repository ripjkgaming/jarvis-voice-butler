'use client';

import { useState } from 'react';
import type { ChessSettings, Snapshot } from '@/lib/chess/types';

type Send = (name: string, args?: Record<string, unknown>) => Promise<unknown>;

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="cx-modal" role="dialog" aria-label={title} onClick={onClose}>
      <div className="cx-modal__card" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>{title}</h2>
          <button type="button" onClick={onClose} aria-label="close">
            ×
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function NewGameDialog({
  snap,
  send,
  onClose,
}: {
  snap: Snapshot;
  send: Send;
  onClose: () => void;
}) {
  const [colour, setColour] = useState(snap.settings.colour);
  const [level, setLevel] = useState(
    snap.settings.difficulty.startsWith('elo:') ? 'club' : snap.settings.difficulty
  );
  const [tc, setTc] = useState(snap.settings.time_control);
  const [custom, setCustom] = useState('');
  const [fen, setFen] = useState('');
  const [pgn, setPgn] = useState('');
  const [error, setError] = useState('');

  const start = async () => {
    setError('');
    if (pgn.trim()) {
      const r = (await send('import_pgn_text', { pgn })) as { ok?: boolean; text?: string } | null;
      if (!r?.ok) return setError(r?.text ?? 'Could not import that PGN.');
      return onClose();
    }
    await send('new', {
      colour,
      preset: level,
      time_control: tc === 'custom' ? custom || 'untimed' : tc,
      fen: fen.trim() || undefined,
    });
    onClose();
  };

  return (
    <Modal title="NEW GAME" onClose={onClose}>
      <div className="cx-form">
        <label>
          Colour
          <select value={colour} onChange={(e) => setColour(e.target.value)}>
            <option>white</option>
            <option>black</option>
            <option>random</option>
          </select>
        </label>
        <label>
          Difficulty
          <select value={level} onChange={(e) => setLevel(e.target.value)}>
            {['novice', 'club', 'strong', 'expert', 'master', 'full'].map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
        <label>
          Time control
          <select value={tc} onChange={(e) => setTc(e.target.value)}>
            {['untimed', '5+0', '10+0', '15+10', 'custom'].map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
        {tc === 'custom' ? (
          <label>
            Minutes+increment
            <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="3+2" />
          </label>
        ) : null}
        <label className="cx-form__wide">
          Start from FEN
          <input value={fen} onChange={(e) => setFen(e.target.value)} placeholder="optional FEN" />
        </label>
        <label className="cx-form__wide">
          …or import a PGN
          <textarea
            value={pgn}
            onChange={(e) => setPgn(e.target.value)}
            rows={3}
            placeholder="paste PGN"
          />
        </label>
        {error ? <p className="cx-form__error">{error}</p> : null}
        <button type="button" className="cx-btn cx-btn--primary" onClick={start}>
          START
        </button>
      </div>
    </Modal>
  );
}

const TOGGLES: [keyof ChessSettings, string][] = [
  ['announce_moves', 'Announce Jarvis’s moves'],
  ['confirm_moves', 'Confirm my spoken moves'],
  ['square_labels', 'Square labels'],
  ['reduce_animations', 'Reduce animations'],
  ['eval_bar', 'Evaluation ring by default'],
  ['remember_analysis', 'Remember my analysis settings'],
  ['commentary_muted', 'Mute commentary'],
  ['online_explanations', 'Online explanations (OpenRouter)'],
  ['speak_explanations', 'Speak explanations (Classic)'],
  ['echo_gate', 'Echo gate (use without headphones)'],
  ['require_prefix', 'Require “chess” before voice commands'],
  ['intro', 'Play the intro'],
];

export function SettingsDialog({
  snap,
  send,
  onClose,
}: {
  snap: Snapshot;
  send: Send;
  onClose: () => void;
}) {
  const s = snap.settings;
  const [models, setModels] = useState(s.models.join(', '));
  const [test, setTest] = useState('');
  const set = (k: keyof ChessSettings, v: unknown) => send('set', { [k]: v });
  return (
    <Modal title="SETTINGS" onClose={onClose}>
      <div className="cx-form cx-form--settings">
        <fieldset>
          <legend>Game</legend>
          <label>
            Stop acknowledgement
            <select value={s.stop_ack} onChange={(e) => set('stop_ack', e.target.value)}>
              <option value="spoken">spoken</option>
              <option value="silent">silent</option>
            </select>
          </label>
          <label>
            Take-over strength
            <select
              value={s.takeover_strength}
              onChange={(e) => set('takeover_strength', e.target.value)}
            >
              {['match', 'novice', 'club', 'strong', 'expert', 'master', 'full'].map((l) => (
                <option key={l}>{l}</option>
              ))}
            </select>
          </label>
          <label>
            Hint default level
            <select
              value={s.hint_default}
              onChange={(e) => set('hint_default', Number(e.target.value))}
            >
              {[1, 2, 3].map((l) => (
                <option key={l}>{l}</option>
              ))}
            </select>
          </label>
          <label>
            Explanations pitched for
            <select value={s.audience} onChange={(e) => set('audience', e.target.value)}>
              <option value="novice">a beginner</option>
              <option value="club">a club player</option>
              <option value="advanced">an advanced player</option>
            </select>
          </label>
          <label>
            Commentary volume
            <input
              type="range"
              min={0.2}
              max={1.5}
              step={0.1}
              value={s.commentary_volume}
              onChange={(e) => set('commentary_volume', Number(e.target.value))}
            />
          </label>
          <label>
            Board size
            <input
              type="range"
              min={0.6}
              max={1.6}
              step={0.1}
              value={s.board_size}
              onChange={(e) => set('board_size', Number(e.target.value))}
            />
          </label>
          <label>
            Intro length
            <select value={s.intro_length} onChange={(e) => set('intro_length', e.target.value)}>
              <option value="full">full</option>
              <option value="short">short (≈4 s)</option>
              <option value="off">off</option>
            </select>
          </label>
        </fieldset>
        <fieldset>
          <legend>Switches</legend>
          {TOGGLES.map(([k, label]) => (
            <label key={k} className="cx-check">
              <input
                type="checkbox"
                checked={Boolean(s[k])}
                onChange={(e) => set(k, e.target.checked)}
              />
              {label}
            </label>
          ))}
        </fieldset>
        <fieldset>
          <legend>AI explanations</legend>
          <label className="cx-form__wide">
            OpenRouter model IDs (fallback order)
            <input
              value={models}
              onChange={(e) => setModels(e.target.value)}
              onBlur={() =>
                set(
                  'models',
                  models
                    .split(',')
                    .map((m) => m.trim())
                    .filter(Boolean)
                )
              }
            />
          </label>
          <p>
            AI calls left today: <b>{snap.ai_left}</b> ·{' '}
            {snap.ai_online ? 'key found' : 'no OPENROUTER_API_KEY — templates only'}
          </p>
          <button
            type="button"
            className="cx-btn"
            onClick={async () => {
              setTest('testing…');
              await send('ai_test');
              setTest('Jarvis will say the result.');
            }}
          >
            Test the commentary connection
          </button>
          {test ? <small>{test}</small> : null}
        </fieldset>
        <fieldset>
          <legend>Voice engine</legend>
          <label>
            Whisper model
            <select value={s.whisper_model} onChange={(e) => set('whisper_model', e.target.value)}>
              <option>base.en</option>
              <option>small.en</option>
            </select>
          </label>
          <label>
            Device
            <select value={s.device} onChange={(e) => set('device', e.target.value)}>
              <option>cpu</option>
              <option>cuda</option>
            </select>
          </label>
          <label>
            End-of-speech silence (ms)
            <input
              type="number"
              min={150}
              max={1200}
              step={50}
              value={s.end_silence_ms}
              onChange={(e) => set('end_silence_ms', Number(e.target.value))}
            />
          </label>
          <label>
            Piper speed (length scale)
            <input
              type="number"
              min={0.6}
              max={1.6}
              step={0.05}
              value={s.piper_length_scale}
              onChange={(e) => set('piper_length_scale', Number(e.target.value))}
            />
          </label>
          <label>
            Microphone device
            <input
              defaultValue={s.mic_device ?? ''}
              placeholder="default"
              onBlur={(e) => set('mic_device', e.target.value || null)}
            />
          </label>
          <label>
            Speaker device
            <input
              defaultValue={s.speaker_device ?? ''}
              placeholder="default"
              onBlur={(e) => set('speaker_device', e.target.value || null)}
            />
          </label>
          <small>Voice engine changes apply the next time Listen is switched on.</small>
        </fieldset>
      </div>
    </Modal>
  );
}

export function SavesDialog({
  snap,
  send,
  onClose,
}: {
  snap: Snapshot;
  send: Send;
  onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [pgn, setPgn] = useState('');
  return (
    <Modal title="SAVED GAMES" onClose={onClose}>
      <div className="cx-form">
        <div className="cx-form__wide cx-row">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="save as…" />
          <button
            type="button"
            className="cx-btn"
            onClick={async () => {
              await send('save', name ? { name } : {});
              setName('');
            }}
          >
            Save
          </button>
        </div>
        <ul className="cx-saves cx-form__wide">
          {snap.saves.length === 0 ? <li>No saved games yet.</li> : null}
          {snap.saves.map((n) => (
            <li key={n}>
              <span>{n.replace(/-/g, ' ')}</span>
              <button
                type="button"
                className="cx-btn"
                onClick={async () => {
                  await send('load', { name: n });
                  onClose();
                }}
              >
                Load
              </button>
              <button
                type="button"
                className="cx-btn cx-btn--danger"
                onClick={() => send('delete_save', { name: n, confirmed: true })}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
        <div className="cx-form__wide cx-row">
          <button
            type="button"
            className="cx-btn"
            onClick={async () => {
              const r = (await send('export_pgn')) as { data?: { pgn?: string } } | null;
              const text = r?.data?.pgn ?? '';
              setPgn(text);
              try {
                await navigator.clipboard.writeText(text);
              } catch {
                /* clipboard may be blocked; the text box below still works */
              }
            }}
          >
            Export PGN
          </button>
          <button
            type="button"
            className="cx-btn"
            onClick={async () => {
              if (pgn.trim()) await send('import_pgn_text', { pgn });
              onClose();
            }}
          >
            Import PGN
          </button>
        </div>
        <textarea
          className="cx-form__wide"
          rows={5}
          value={pgn}
          onChange={(e) => setPgn(e.target.value)}
          placeholder="PGN text appears here / paste to import"
        />
      </div>
    </Modal>
  );
}

export const SHORTCUTS: [string, string][] = [
  ['type e2e4 / Nf3 + Enter', 'Move (command bar is always focused)'],
  ['Ctrl+Z / Ctrl+Y', 'Undo / redo'],
  ['F', 'Flip board'],
  ['H', 'Hint (once, escalates)'],
  ['A', 'Analyse position (once)'],
  ['E', 'Explain last move (once)'],
  ['T', 'Take over / take back'],
  ['Space', 'Pause / resume (spectator, or after a Jarvis call)'],
  ['Esc', 'STOP everything'],
  ['L', 'Square labels on/off'],
  ['Ctrl+S / Ctrl+O', 'Save / load'],
  ['Ctrl+N', 'New game'],
  ['F11', 'Fullscreen'],
  ['?', 'This overlay'],
  ['Q', 'Quit (with the ASCII outro)'],
];

export function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="KEYBOARD" onClose={onClose}>
      <table className="cx-keys">
        <tbody>
          {SHORTCUTS.map(([k, v]) => (
            <tr key={k}>
              <th>{k}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="cx-keys__note">
        With the command bar focused use Shift+F / H / A / E / T / L, or press Tab / click the board
        to leave the bar and use the plain letters. Ctrl+Q quits from anywhere.
      </p>
    </Modal>
  );
}
