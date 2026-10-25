'use client';

import { cls, evalLabel, evalToUnit, formatClock } from '@/lib/chess/pieces';
import type { Snapshot } from '@/lib/chess/types';
import { PieceArt } from './piece-art';

const ORDER = ['q', 'r', 'b', 'n', 'p'] as const;

export function Clock({ snap, colour }: { snap: Snapshot; colour: 'white' | 'black' }) {
  const active = snap.turn === colour && !snap.result && snap.clock.running && !snap.clock.paused;
  const secs = snap.clock[colour];
  const low = snap.clock.timed && secs < 30;
  return (
    <div className={cls('cx-clock', active && 'cx-clock--on', low && 'cx-clock--low')}>
      <span className="cx-clock__who">
        {colour === snap.human_colour ? 'YOU' : 'JARVIS'} · {colour}
      </span>
      <span className="cx-clock__time">{snap.clock.timed ? formatClock(secs) : '--:--'}</span>
      {snap.clock.paused && snap.clock.timed ? <em>paused</em> : null}
    </div>
  );
}

export function CapturedPieces({ snap }: { snap: Snapshot }) {
  const row = (side: 'white' | 'black') => {
    // pieces `side` has captured = the opponent's missing pieces
    const lost = snap.captured[side === 'white' ? 'black' : 'white'];
    const items = ORDER.flatMap((t) => lost.filter((x) => x === t).map(() => t));
    const mat = snap.material.diff * (side === 'white' ? 1 : -1);
    return (
      <div className="cx-captured" key={side}>
        {items.map((t, i) => (
          <PieceArt
            key={`${side}${t}${i}`}
            type={t}
            className={`cx-captured__p cx-piece--${side === 'white' ? 'black' : 'white'}`}
          />
        ))}
        {mat > 0 ? <b>+{mat}</b> : null}
      </div>
    );
  };
  return (
    <div className="cx-capturedwrap" aria-label="captured pieces">
      {row('white')}
      {row('black')}
    </div>
  );
}

/** Arc-reactor eval ring. Blank until the user asks for an evaluation. */
export function EvalRing({ snap }: { snap: Snapshot }) {
  const ev = snap.eval;
  const r = 44;
  const c = 2 * Math.PI * r;
  const unit = ev ? evalToUnit(ev.cp, ev.mate) : 0;
  const humanWhite = snap.human_colour === 'white';
  const mine = humanWhite ? unit : 1 - unit;
  const sign = humanWhite ? 1 : -1;
  const mineCp = ev && ev.cp !== null ? ev.cp * sign : null;
  const mineMate = ev && ev.mate !== null ? ev.mate * sign : null;
  return (
    <div
      className={cls('cx-eval', ev && 'cx-eval--on')}
      title={ev ? 'engine evaluation' : 'evaluation off — ask for it'}
    >
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="50" r={r} className="cx-eval__track" />
        {ev ? (
          <circle
            cx="50"
            cy="50"
            r={r}
            className="cx-eval__arc"
            strokeDasharray={`${c * mine} ${c}`}
            transform="rotate(-90 50 50)"
          />
        ) : null}
        <circle cx="50" cy="50" r="30" className="cx-eval__core" />
        <circle cx="50" cy="50" r="13" className="cx-eval__hub" />
      </svg>
      <div className="cx-eval__read">
        {ev ? (
          <>
            <b>{evalLabel(mineCp, mineMate)}</b>
            {ev.best ? <small>best {ev.best}</small> : null}
          </>
        ) : (
          <small>EVAL</small>
        )}
      </div>
    </div>
  );
}

export function MoveList({ snap }: { snap: Snapshot }) {
  const rows: { n: number; w?: string; b?: string; wi: number; bi: number }[] = [];
  snap.moves.forEach((m, i) => {
    const n = Math.floor(i / 2);
    rows[n] = rows[n] ?? { n: n + 1, wi: n * 2 + 1, bi: n * 2 + 2 };
    if (i % 2 === 0) rows[n].w = m;
    else rows[n].b = m;
  });
  const badge = (ply: number) => {
    const l = snap.badges[String(ply)];
    return l ? <i className={`cx-mq cx-mq--${l.toLowerCase()}`} title={l} /> : null;
  };
  return (
    <ol className="cx-moves" aria-label="moves">
      {rows.map((r) => (
        <li key={r.n}>
          <span className="cx-moves__n">{r.n}.</span>
          <span>
            {r.w}
            {r.w ? badge(r.wi) : null}
          </span>
          <span>
            {r.b}
            {r.b ? badge(r.bi) : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function MicIndicator({ snap }: { snap: Snapshot }) {
  const state = snap.jarvis_active ? 'paused' : snap.speaking ? 'speaking' : snap.mic_state;
  const label =
    {
      off: 'MIC OFF',
      listening: 'LISTENING',
      hearing: 'HEARING',
      thinking: 'THINKING',
      speaking: 'SPEAKING',
      paused: 'PAUSED',
    }[state] ?? state.toUpperCase();
  return (
    <div className={`cx-mic cx-mic--${state}`} title="local microphone and Piper voice">
      <span className="cx-mic__eq" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </span>
      <span>{label}</span>
    </div>
  );
}

export function StatusChip({ snap }: { snap: Snapshot }) {
  const status = snap.status;
  const tone =
    status === 'TAKE-OVER' || status === 'JARVIS ACTIVE'
      ? 'amber'
      : status === 'GAME OVER'
        ? 'red'
        : 'cyan';
  return (
    <div className={`cx-status cx-status--${tone}`}>
      {snap.thinking ? <span className="cx-radar" aria-hidden="true" /> : null}
      <b>{status}</b>
      {snap.analysis_on ? <span className="cx-badge-analysis">ANALYSIS ON</span> : null}
    </div>
  );
}

export function CommentaryPanel({ snap }: { snap: Snapshot }) {
  const p = snap.panel;
  const hint = snap.hint;
  if (!p && !hint) return null;
  return (
    <div className="cx-commentary">
      {hint ? (
        <p className="cx-commentary__hint">
          <b>HINT {hint.level}/3</b> {hint.text}
        </p>
      ) : null}
      {p ? (
        <>
          {p.headline ? <h3>{p.headline}</h3> : null}
          {p.detail ? <p>{p.detail}</p> : null}
          <small>
            {p.source === 'llm'
              ? `explained by ${p.model ?? 'the commentary model'}`
              : p.source === 'engine'
                ? 'engine'
                : 'offline notes'}
          </small>
        </>
      ) : null}
    </div>
  );
}

export function ReviewPanel({ snap }: { snap: Snapshot }) {
  const r = snap.review;
  if (!r) return null;
  const w = 220;
  const h = 56;
  const pts = r.graph.map((v, i) => {
    const x = r.graph.length > 1 ? (i / (r.graph.length - 1)) * w : 0;
    const y = h / 2 - (Math.max(-1000, Math.min(1000, v)) / 1000) * (h / 2 - 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <div className="cx-review">
      <h3>REVIEW</h3>
      <p>
        Accuracy <b>{r.accuracy}%</b> · mistakes <b>{r.mistakes}</b>
      </p>
      <svg viewBox={`0 0 ${w} ${h}`} className="cx-review__graph" aria-label="evaluation graph">
        <line x1="0" y1={h / 2} x2={w} y2={h / 2} />
        <polyline points={pts.join(' ')} />
      </svg>
      <ul>
        {r.moments.map((m, i) => (
          <li key={m.ply} className={i === r.index ? 'on' : ''}>
            <b>
              {m.move_no}. {m.san}
            </b>{' '}
            <em>{m.label}</em> — {m.text}
          </li>
        ))}
      </ul>
    </div>
  );
}
