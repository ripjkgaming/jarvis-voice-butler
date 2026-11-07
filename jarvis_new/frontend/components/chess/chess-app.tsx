'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { type Tracked, cls, syncPieces } from '@/lib/chess/pieces';
import { useGames } from '@/lib/chess/use-games';
import { invoke, isTauri } from '@/lib/tauri';
import { Board } from './board';
import './chess.css';
import { NewGameDialog, SavesDialog, SettingsDialog, ShortcutsOverlay } from './dialogs';
import {
  CapturedPieces,
  Clock,
  CommentaryPanel,
  EvalRing,
  MicIndicator,
  MoveList,
  ReviewPanel,
  StatusChip,
} from './side-panel';

type Dialog = 'new' | 'settings' | 'saves' | 'keys' | null;

const SLASH: Record<string, string> = {
  hint: 'hint',
  analyse: 'analyse',
  analyze: 'analyse',
  explain: 'explain',
  takeover: 'takeover',
  stop: 'stop',
  undo: 'undo',
  redo: 'redo',
  flip: 'flip',
  save: 'save',
  quit: 'quit',
  review: 'review',
  new: 'new_game',
};

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const q = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(q.matches);
    const on = (e: MediaQueryListEvent) => setReduced(e.matches);
    q.addEventListener('change', on);
    return () => q.removeEventListener('change', on);
  }, []);
  return reduced;
}

export function ChessApp() {
  const { snap, connected, send } = useGames();
  const prefersReduced = usePrefersReducedMotion();
  const [tracked, setTracked] = useState<Tracked | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [text, setText] = useState('');
  const [toast, setToast] = useState('');
  const barRef = useRef<HTMLInputElement>(null);
  const fsSeen = useRef(0);
  const lastKey = snap ? `${snap.fen}|${snap.uci_moves.join(' ')}` : '';

  useEffect(() => {
    if (!snap) return;
    setTracked((prev) => syncPieces(prev, snap.fen, snap.uci_moves));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastKey]);

  const voice = snap?.ui_mode === 'voice';
  const reduced = prefersReduced || Boolean(snap?.settings.reduce_animations);

  const say = useCallback((m: string) => {
    setToast(m);
    setTimeout(() => setToast((t) => (t === m ? '' : t)), 3200);
  }, []);

  const run = useCallback(
    async (name: string, args: Record<string, unknown> = {}) => {
      const r = await send(name, args);
      if (r && !r.ok && (r.say || r.text)) say(r.say ?? r.text ?? '');
      return r;
    },
    [send, say]
  );

  // Fullscreen requests from the server ("fullscreen" by voice).
  useEffect(() => {
    const f = snap?.flash;
    if (!f?.ui || f.id === fsSeen.current) return;
    fsSeen.current = f.id;
    if (f.ui.cmd === 'fullscreen') {
      if (f.ui.on) document.documentElement.requestFullscreen?.().catch(() => undefined);
      else if (document.fullscreenElement) document.exitFullscreen?.().catch(() => undefined);
    }
  }, [snap?.flash]);

  const quit = useCallback(async () => {
    await send('quit');
    if (isTauri()) invoke('close_chess_window').catch(() => undefined);
  }, [send]);

  // Escape stops dialogue in both modes; Space resumes after a Jarvis call.
  useEffect(() => {
    if (!snap) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        void run('stop');
        setDialog(null);
        return;
      }
      if (voice) {
        if (e.code === 'Space' && (snap.jarvis_active || snap.paused)) {
          e.preventDefault();
          void send('resume_listening');
        }
        return;
      }
      const inBar = document.activeElement === barRef.current;
      const empty = text.length === 0;
      const mod = e.ctrlKey || e.metaKey;
      const k = e.key;
      if (mod) {
        const lower = k.toLowerCase();
        const map: Record<string, () => void> = {
          z: () => void run('undo'),
          y: () => void run('redo'),
          s: () => setDialog('saves'),
          o: () => setDialog('saves'),
          n: () => setDialog('new'),
          q: () => void quit(),
        };
        if (map[lower]) {
          e.preventDefault();
          map[lower]();
        }
        return;
      }
      if (k === '?' && (empty || !inBar)) {
        e.preventDefault();
        setDialog('keys');
        return;
      }
      if (k === ' ' && (!inBar || empty)) {
        if (snap.mode === 'spectate') {
          e.preventDefault();
          void run(snap.spectate.paused ? 'ctl_resume' : 'ctl_pause');
        } else if (snap.jarvis_active) {
          e.preventDefault();
          void send('resume_listening');
        } else if (!inBar) {
          e.preventDefault();
          void run(snap.paused ? 'resume_game' : 'pause_game');
        }
        return;
      }
      // Plain letters only while the command bar is not focused; with it
      // focused (and empty) the Shift variants work, so SAN typing is safe.
      const letter = k.length === 1 ? k : '';
      const usable = !inBar || (empty && e.shiftKey && 'FHAETL'.includes(letter));
      if (!usable || !letter) return;
      const table: Record<string, () => void> = {
        f: () => void run('flip'),
        h: () => void run('hint'),
        a: () => void run('analyse'),
        e: () => void run('explain'),
        t: () => void run(snap.mode === 'play' ? 'takeover' : 'handback'),
        l: () => void run('labels'),
        q: () => void quit(),
      };
      const fn = table[letter.toLowerCase()];
      if (fn) {
        e.preventDefault();
        fn();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [snap, voice, text, run, send, quit]);

  // Keep the command bar focused unless the user is in a dialog or leaves it.
  useEffect(() => {
    if (!voice && !dialog) barRef.current?.focus();
  }, [voice, dialog, snap?.fen]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    setText('');
    if (!t) return;
    if (t.startsWith('/')) {
      const [cmd, ...rest] = t.slice(1).split(/\s+/);
      const name = SLASH[cmd.toLowerCase()];
      if (name) {
        await run(name, rest.length && name === 'save' ? { name: rest.join(' ') } : {});
        return;
      }
    }
    const r = await send('say', { text: t, source: 'keyboard' });
    if (r?.say) say(r.say);
  };

  const scale = snap?.settings.board_size ?? 1;
  const style = useMemo(() => ({ ['--cx-scale' as string]: String(scale) }), [scale]);

  if (!snap || !tracked) {
    return (
      <main className="cx-root cx-root--wait">
        <div className="cx-wait">
          <div className="cx-radar cx-radar--big" aria-hidden="true" />
          <p>
            {connected
              ? 'Setting up the board…'
              : 'Waiting for the games server on 127.0.0.1:4320…'}
          </p>
          <small>
            Start a game with <code>chess</code> or <code>chess --voice</code> in Konsole.
          </small>
        </div>
      </main>
    );
  }

  const takeover = snap.mode === 'takeover';
  const over = snap.result;
  const humanWhite = snap.human_colour === 'white';
  const top = humanWhite ? 'black' : 'white';
  const bottom = humanWhite ? 'white' : 'black';
  const onMove = (from: string, to: string, promotion?: string) =>
    void run('move', { from, to, promotion });

  const resultLine = (() => {
    if (!over) return '';
    if (over.outcome === '1/2-1/2') return 'DRAW';
    const whiteWon = over.outcome === '1-0';
    return whiteWon === humanWhite ? 'YOU WIN' : 'JARVIS WINS';
  })();

  return (
    <main
      className={cls(
        'cx-root',
        voice && 'cx-root--voice',
        takeover && 'cx-root--takeover',
        over && 'cx-root--over'
      )}
      style={style}
      data-state={snap.status}
    >
      {isTauri() ? (
        <header className="cx-chrome" data-tauri-drag-region>
          <span data-tauri-drag-region>♞ JARVIS CHESS</span>
          <button
            type="button"
            onClick={() => invoke('close_chess_window').catch(() => undefined)}
            aria-label="close"
          >
            ×
          </button>
        </header>
      ) : null}

      {snap.banner ? <div className="cx-banner cx-banner--jarvis">{snap.banner}</div> : null}
      {takeover ? (
        <div className="cx-banner cx-banner--takeover">
          JARVIS IN CONTROL{snap.takeover_remaining ? ` · ${snap.takeover_remaining} left` : ''}
        </div>
      ) : null}
      {snap.mode === 'spectate' ? (
        <div className="cx-banner cx-banner--spectate">
          SPECTATING · {snap.spectate.speed}×{snap.spectate.paused ? ' · paused' : ''}
        </div>
      ) : null}

      {!voice ? (
        <nav className="cx-bar" aria-label="game controls">
          <button type="button" className="cx-btn" onClick={() => setDialog('new')}>
            New
          </button>
          <button
            type="button"
            className="cx-btn"
            disabled={!snap.can_undo}
            onClick={() => void run('undo')}
          >
            Undo
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('flip')}>
            Flip
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('hint')}>
            Hint
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('analyse')}>
            Analyse
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('explain')}>
            Explain
          </button>
          <button
            type="button"
            className={cls('cx-btn', takeover && 'cx-btn--amber')}
            onClick={() => void run(snap.mode === 'play' ? 'takeover' : 'handback')}
          >
            {snap.mode === 'play' ? 'Take Over' : 'Take Back'}
          </button>
          <button type="button" className="cx-btn cx-btn--stop" onClick={() => void run('stop')}>
            Stop
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('review')}>
            Review
          </button>
          <button type="button" className="cx-btn" onClick={() => setDialog('saves')}>
            Save
          </button>
          <button type="button" className="cx-btn" onClick={() => setDialog('settings')}>
            Settings
          </button>
          <button
            type="button"
            className={cls('cx-btn', snap.listening && 'cx-btn--on')}
            onClick={() => void run('listen', { on: !snap.listening })}
          >
            {snap.listening ? 'Listen: on' : 'Listen'}
          </button>
          <button
            type="button"
            className="cx-btn cx-btn--quiet"
            onClick={() => setDialog('keys')}
            aria-label="keyboard shortcuts"
          >
            ?
          </button>
        </nav>
      ) : (
        <div className="cx-voicebar">
          <b>VOICE MODE</b>
          <button type="button" className="cx-btn cx-btn--stop" onClick={() => void run('stop')}>
            Stop
          </button>
          <button
            type="button"
            className="cx-btn"
            aria-pressed={snap.settings.commentary_muted}
            onClick={() => void run('set', { commentary_muted: !snap.settings.commentary_muted })}
          >
            {snap.settings.commentary_muted ? 'Unmute' : 'Mute'}
          </button>
          <span>
            say “knight to f three”, “castle kingside”, “hint”, “stop” — squares are labelled
          </span>
        </div>
      )}

      {snap.mode === 'spectate' && !voice ? (
        <div className="cx-spec">
          <button
            type="button"
            className="cx-btn"
            onClick={() => void run(snap.spectate.paused ? 'ctl_resume' : 'ctl_pause')}
          >
            {snap.spectate.paused ? 'Play' : 'Pause'}
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('ctl_next')}>
            Step
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('ctl_slower')}>
            Slower
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('ctl_faster')}>
            Faster
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('takeover_finish')}>
            Finish for me
          </button>
          <button type="button" className="cx-btn" onClick={() => void run('handback')}>
            Join in
          </button>
        </div>
      ) : null}

      <div className="cx-main">
        <section className="cx-stage">
          <Board
            snap={snap}
            pieces={tracked.pieces}
            event={tracked.event}
            epoch={tracked.epoch}
            interactive={!voice}
            reduced={reduced}
            showLegend={voice || snap.settings.square_labels}
            onMove={onMove}
          />
          {snap.caption || snap.heard ? (
            <div className="cx-caption" aria-live="polite">
              {snap.caption || `“${snap.heard}”`}
            </div>
          ) : null}
          {snap.pending?.type === 'disambiguate' ? (
            <div className="cx-ask">Which one? Say 1, 2 or the square.</div>
          ) : null}
          {toast ? <div className="cx-toast">{toast}</div> : null}
        </section>

        <aside className="cx-side">
          <Clock snap={snap} colour={top} />
          <div className="cx-side__row">
            <StatusChip snap={snap} />
            <MicIndicator snap={snap} />
          </div>
          <div className="cx-side__ring">
            <EvalRing snap={snap} />
            <div className="cx-material">
              <span>material</span>
              <b>
                {snap.material.diff === 0
                  ? 'level'
                  : `${snap.material.diff > 0 === humanWhite ? 'you' : 'Jarvis'} +${Math.abs(snap.material.diff)}`}
              </b>
              <small>
                {snap.engine === 'stockfish'
                  ? 'Stockfish'
                  : snap.engine
                    ? 'built-in engine (install stockfish)'
                    : ''}{' '}
                · {snap.preset}
              </small>
            </div>
          </div>
          <CapturedPieces snap={snap} />
          <MoveList snap={snap} />
          <CommentaryPanel snap={snap} />
          <ReviewPanel snap={snap} />
          <Clock snap={snap} colour={bottom} />
        </aside>
      </div>

      {!voice ? (
        <form className="cx-cmd" onSubmit={submit}>
          <span aria-hidden="true">&gt;</span>
          <input
            ref={barRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="e2e4, Nf3, /hint …  (Esc stops everything)"
            autoComplete="off"
            spellCheck={false}
            aria-label="command bar"
          />
        </form>
      ) : null}

      {over ? (
        <div className="cx-result" role="status">
          <h2>{resultLine}</h2>
          <p>
            {over.reason} · {Math.ceil(snap.moves.length / 2)} moves
          </p>
          {!voice ? (
            <div className="cx-row">
              <button
                type="button"
                className="cx-btn cx-btn--primary"
                onClick={() => void run('new_game')}
              >
                Rematch
              </button>
              <button type="button" className="cx-btn" onClick={() => void run('review')}>
                Review
              </button>
              <button type="button" className="cx-btn" onClick={() => void quit()}>
                Quit
              </button>
            </div>
          ) : (
            <p className="cx-result__say">say “rematch”, “review the game” or “quit chess”</p>
          )}
        </div>
      ) : null}

      {dialog === 'new' ? (
        <NewGameDialog snap={snap} send={send} onClose={() => setDialog(null)} />
      ) : null}
      {dialog === 'settings' ? (
        <SettingsDialog snap={snap} send={send} onClose={() => setDialog(null)} />
      ) : null}
      {dialog === 'saves' ? (
        <SavesDialog snap={snap} send={send} onClose={() => setDialog(null)} />
      ) : null}
      {dialog === 'keys' ? <ShortcutsOverlay onClose={() => setDialog(null)} /> : null}
    </main>
  );
}
