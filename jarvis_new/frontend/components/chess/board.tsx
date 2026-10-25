'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  FILES,
  type MoveEvent,
  type PieceInst,
  cls,
  isPromotion,
  legalMap,
  sqCoords,
  sqName,
} from '@/lib/chess/pieces';
import type { PieceType, Snapshot } from '@/lib/chess/types';
import { EffectsCanvas, type FxEvent } from './effects-canvas';
import { PieceArt } from './piece-art';

const NATO = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel'];
const PROMO: PieceType[] = ['q', 'r', 'b', 'n'];

interface Props {
  snap: Snapshot;
  pieces: PieceInst[];
  event: MoveEvent | null;
  epoch: number;
  /** Mouse and keyboard play (Classic Mode). Voice Mode is display only. */
  interactive: boolean;
  reduced: boolean;
  showLegend: boolean;
  onMove: (from: string, to: string, promotion?: string) => void;
}

interface Arrow {
  from: string;
  to: string;
}

function pos(sq: string, flipped: boolean) {
  const { file, rank } = sqCoords(sq);
  return { col: flipped ? 7 - file : file, row: flipped ? rank : 7 - rank };
}

/** Squares strictly between two squares on a rank, file or diagonal. */
function between(a: string, b: string): string[] {
  const A = sqCoords(a);
  const B = sqCoords(b);
  const df = Math.sign(B.file - A.file);
  const dr = Math.sign(B.rank - A.rank);
  const straight = A.file === B.file || A.rank === B.rank;
  const diag = Math.abs(B.file - A.file) === Math.abs(B.rank - A.rank);
  if (!straight && !diag) return [];
  const out: string[] = [];
  let f = A.file + df;
  let r = A.rank + dr;
  while (f !== B.file || r !== B.rank) {
    out.push(sqName(f, r));
    f += df;
    r += dr;
  }
  return out;
}

export function Board({
  snap,
  pieces,
  event,
  epoch,
  interactive,
  reduced,
  showLegend,
  onMove,
}: Props) {
  const flipped = snap.flipped;
  const ref = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ id: number; x: number; y: number; moved: boolean } | null>(
    null
  );
  const [marks, setMarks] = useState<string[]>([]);
  const [arrows, setArrows] = useState<Arrow[]>([]);
  const [rightStart, setRightStart] = useState<string | null>(null);
  const [promo, setPromo] = useState<{ from: string; to: string } | null>(null);
  const [flashSq, setFlashSq] = useState<string[]>([]);
  const [intent, setIntent] = useState<{ from: string; to: string } | null>(null);
  const [fx, setFx] = useState<FxEvent | null>(null);
  const lastFlash = useRef(0);
  const lastArrow = useRef(0);
  const lastEvent = useRef(0);

  const legal = useMemo(() => legalMap(snap.legal), [snap.legal]);
  const pieceAt = useMemo(() => new Map(pieces.map((p) => [p.sq, p])), [pieces]);
  const myTurn =
    interactive &&
    snap.mode === 'play' &&
    !snap.result &&
    !snap.paused &&
    !snap.jarvis_active &&
    snap.turn === snap.human_colour;

  // New position from scratch (new game / load / undo): clear local marks.
  useEffect(() => {
    setSelected(null);
    setPromo(null);
    setDrag(null);
  }, [epoch, snap.fen]);

  // The server's red-path flash for illegal moves.
  useEffect(() => {
    const f = snap.flash;
    if (!f || f.id === lastFlash.current || f.kind !== 'illegal') return;
    lastFlash.current = f.id;
    const sqs = new Set<string>(f.squares);
    if (f.squares.length === 2) for (const s of between(f.squares[0], f.squares[1])) sqs.add(s);
    setFlashSq([...sqs]);
    const t = setTimeout(() => setFlashSq([]), 750);
    return () => clearTimeout(t);
  }, [snap.flash]);

  // A cyan intent arrow for ~200 ms before the heard move lands.
  useEffect(() => {
    const a = snap.arrow;
    if (!a || a.id === lastArrow.current) return;
    lastArrow.current = a.id;
    setIntent({ from: a.from, to: a.to });
    const t = setTimeout(() => setIntent(null), 260);
    return () => clearTimeout(t);
  }, [snap.arrow]);

  // Capture shatter and promotion burst.
  useEffect(() => {
    if (!event || event.seq === lastEvent.current) return;
    lastEvent.current = event.seq;
    const target = event.captured?.sq ?? event.to;
    const { col, row } = pos(target, flipped);
    const colour = event.captured?.colour === 'white' ? '#22d3ee' : '#f59e0b';
    if (event.captured)
      setFx({ id: event.seq * 3, kind: 'shatter', x: (col + 0.5) / 8, y: (row + 0.5) / 8, colour });
    else if (event.promotion) {
      const p = pos(event.to, flipped);
      setFx({
        id: event.seq * 3 + 1,
        kind: 'promote',
        x: (p.col + 0.5) / 8,
        y: (p.row + 0.5) / 8,
        colour: '#ffffff',
      });
    }
    if (event.promotion && event.captured) {
      const p = pos(event.to, flipped);
      setTimeout(
        () =>
          setFx({
            id: event.seq * 3 + 2,
            kind: 'promote',
            x: (p.col + 0.5) / 8,
            y: (p.row + 0.5) / 8,
            colour: '#ffffff',
          }),
        120
      );
    }
  }, [event, flipped]);

  // Checkmate: radial burst from the mated king.
  const mated = snap.result?.reason === 'checkmate';
  const loserKing = useMemo(() => {
    if (!mated || !snap.result) return null;
    const loser = snap.result.outcome === '1-0' ? 'black' : 'white';
    return pieces.find((p) => p.type === 'k' && p.colour === loser) ?? null;
  }, [mated, snap.result, pieces]);
  useEffect(() => {
    if (!loserKing) return;
    const { col, row } = pos(loserKing.sq, flipped);
    const t = setTimeout(
      () =>
        setFx({
          id: 900000 + snap.moves.length,
          kind: 'mate',
          x: (col + 0.5) / 8,
          y: (row + 0.5) / 8,
          colour: '#ef4444',
        }),
      450
    );
    return () => clearTimeout(t);
  }, [loserKing, flipped, snap.moves.length]);

  const squareAt = useCallback(
    (clientX: number, clientY: number): string | null => {
      const el = ref.current;
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const x = (clientX - r.left) / r.width;
      const y = (clientY - r.top) / r.height;
      if (x < 0 || y < 0 || x >= 1 || y >= 1) return null;
      const col = Math.floor(x * 8);
      const row = Math.floor(y * 8);
      return sqName(flipped ? 7 - col : col, flipped ? row : 7 - row);
    },
    [flipped]
  );

  const tryMove = useCallback(
    (from: string, to: string) => {
      if (isPromotion(snap.legal, from, to)) {
        setPromo({ from, to });
        return;
      }
      onMove(from, to);
      setSelected(null);
    },
    [onMove, snap.legal]
  );

  const onPointerDown = (e: React.PointerEvent) => {
    if (!interactive) return;
    const sq = squareAt(e.clientX, e.clientY);
    if (!sq) return;
    if (e.button === 2) {
      setRightStart(sq);
      return;
    }
    if (e.button !== 0 || !myTurn) return;
    if (promo) return;
    const p = pieceAt.get(sq);
    if (selected && legal.get(selected)?.includes(sq)) {
      tryMove(selected, sq);
      return;
    }
    setMarks([]);
    setArrows([]);
    if (p && p.colour === snap.human_colour && legal.has(sq)) {
      setSelected(sq);
      setDrag({ id: p.id, x: e.clientX, y: e.clientY, moved: false });
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } else {
      setSelected(null);
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    setDrag({ ...drag, x: e.clientX, y: e.clientY, moved: true });
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const sq = squareAt(e.clientX, e.clientY);
    if (e.button === 2 && rightStart) {
      if (sq && sq === rightStart)
        setMarks((m) => (m.includes(sq) ? m.filter((x) => x !== sq) : [...m, sq]));
      else if (sq)
        setArrows((a) =>
          a.some((x) => x.from === rightStart && x.to === sq)
            ? a.filter((x) => !(x.from === rightStart && x.to === sq))
            : [...a, { from: rightStart, to: sq }]
        );
      setRightStart(null);
      return;
    }
    if (!drag) return;
    const from = selected;
    const moved = drag.moved;
    setDrag(null);
    if (!from || !moved) return;
    if (sq && sq !== from && legal.get(from)?.includes(sq)) tryMove(from, sq);
    else if (sq && sq !== from) {
      // snap back, and show the blocked path in red
      setFlashSq([...new Set([from, sq, ...between(from, sq)])]);
      setTimeout(() => setFlashSq([]), 750);
    }
  };

  const lastTo = snap.last_move?.to;
  const lastFrom = snap.last_move?.from;
  const targets = selected ? (legal.get(selected) ?? []) : [];
  const dur = reduced ? 0 : 0.22;

  const squares: React.ReactNode[] = [];
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const file = flipped ? 7 - col : col;
      const rank = flipped ? row : 7 - row;
      const sq = sqName(file, rank);
      const dark = (file + rank) % 2 === 0;
      const sqClass = [
        'cx-sq',
        dark ? 'cx-sq--dark' : 'cx-sq--light',
        sq === lastFrom || sq === lastTo ? 'cx-sq--last' : '',
        sq === selected ? 'cx-sq--sel' : '',
        snap.check === sq ? 'cx-sq--check' : '',
        flashSq.includes(sq) ? 'cx-sq--flash' : '',
        marks.includes(sq) ? 'cx-sq--mark' : '',
        snap.highlights.includes(sq) ? 'cx-sq--hl' : '',
        snap.hint && (snap.hint.from === sq || snap.hint.to === sq) ? 'cx-sq--hint' : '',
      ]
        .filter(Boolean)
        .join(' ');
      const delay = reduced ? 0 : (file + (7 - rank)) * 0.045;
      const isTarget = targets.includes(sq);
      const occupied = pieceAt.has(sq);
      squares.push(
        <div
          key={`${epoch}-${sq}`}
          className={sqClass}
          data-sq={sq}
          style={{ animationDelay: `${delay}s` }}
        >
          {isTarget ? <i className={occupied ? 'cx-ring' : 'cx-dot'} /> : null}
          {flashSq.includes(sq) ? <i className="cx-red" /> : null}
        </div>
      );
    }
  }

  const labels: React.ReactNode[] = [];
  if (snap.settings.square_labels) {
    for (let row = 0; row < 8; row++) {
      for (let col = 0; col < 8; col++) {
        const file = flipped ? 7 - col : col;
        const rank = flipped ? row : 7 - row;
        const sq = sqName(file, rank);
        labels.push(
          <span
            key={sq}
            className={cls('cx-code', snap.heard_squares.includes(sq) && 'cx-code--heard')}
            style={{ left: `${col * 12.5}%`, top: `${row * 12.5}%` }}
          >
            {sq}
          </span>
        );
      }
    }
  }

  const arrowEl = (a: Arrow, key: string, cls: string) => {
    const f = pos(a.from, flipped);
    const t = pos(a.to, flipped);
    return (
      <line
        key={key}
        className={cls}
        x1={(f.col + 0.5) * 12.5}
        y1={(f.row + 0.5) * 12.5}
        x2={(t.col + 0.5) * 12.5}
        y2={(t.row + 0.5) * 12.5}
        markerEnd="url(#cx-head)"
      />
    );
  };

  const files = flipped ? [...FILES].reverse() : [...FILES];
  const ranks = flipped ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];

  return (
    <div
      className={cls(
        'cx-boardwrap',
        snap.mode === 'takeover' && 'cx-boardwrap--takeover',
        snap.check && 'cx-boardwrap--check',
        mated && 'cx-boardwrap--mate'
      )}
      data-interactive={interactive ? 'true' : 'false'}
    >
      <div className="cx-ruler cx-ruler--top">
        {files.map((f) => (
          <span key={f}>{f}</span>
        ))}
      </div>
      <div className="cx-ruler cx-ruler--bottom">
        {files.map((f) => (
          <span key={f}>
            {f}
            {showLegend ? <small>{NATO[FILES.indexOf(f)]}</small> : null}
          </span>
        ))}
      </div>
      <div className="cx-ruler cx-ruler--left">
        {ranks.map((r) => (
          <span key={r}>{r}</span>
        ))}
      </div>
      <div className="cx-ruler cx-ruler--right">
        {ranks.map((r) => (
          <span key={r}>{r}</span>
        ))}
      </div>

      <div
        ref={ref}
        className="cx-board"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onContextMenu={(e) => e.preventDefault()}
        style={{ touchAction: 'none' }}
      >
        <div className="cx-squares">{squares}</div>

        <div className="cx-pieces">
          <AnimatePresence initial={false}>
            {pieces.map((p) => {
              const { col, row } = pos(p.sq, flipped);
              const isKnightMove = event?.piece === 'n' && event.to === p.sq && !event.castle;
              const fromPos =
                event && event.to === p.sq && event.piece === p.type
                  ? pos(event.from, flipped)
                  : null;
              const dragging = drag?.id === p.id && drag.moved;
              const mate = loserKing?.id === p.id;
              const x = `${col * 100}%`;
              const y = `${row * 100}%`;
              const animate =
                isKnightMove && fromPos && !reduced
                  ? {
                      x: [`${fromPos.col * 100}%`, x],
                      y: [`${fromPos.row * 100}%`, `${(fromPos.row + row) * 50 - 90}%`, y],
                    }
                  : { x, y };
              return (
                <motion.div
                  key={p.id}
                  className={cls(
                    'cx-piece',
                    `cx-piece--${p.colour}`,
                    dragging && 'cx-piece--drag',
                    snap.disambiguation.some((d) => d.from === p.sq) && 'cx-piece--pulse'
                  )}
                  initial={false}
                  animate={{
                    ...animate,
                    rotate: mate ? 82 : 0,
                    opacity: mate ? 0.55 : 1,
                  }}
                  exit={{
                    opacity: 0,
                    scale: reduced ? 1 : 0.4,
                    transition: { duration: reduced ? 0 : 0.18 },
                  }}
                  transition={{
                    duration: mate ? (reduced ? 0 : 0.7) : dur,
                    delay: mate && !reduced ? 0.35 : 0,
                    ease: 'easeInOut',
                    times: isKnightMove && !reduced ? [0, 0.5, 1] : undefined,
                  }}
                  style={{ width: '12.5%', height: '12.5%' }}
                >
                  <PieceArt type={p.type} className="cx-piece__art" />
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>

        <div className="cx-labels">{labels}</div>

        <svg className="cx-arrows" viewBox="0 0 100 100" aria-hidden="true">
          <defs>
            <marker
              id="cx-head"
              viewBox="0 0 10 10"
              refX="7"
              refY="5"
              markerWidth="4"
              markerHeight="4"
              orient="auto"
            >
              <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
            </marker>
          </defs>
          {arrows.map((a) => arrowEl(a, `u-${a.from}${a.to}`, 'cx-arrow cx-arrow--user'))}
          {intent ? arrowEl(intent, 'intent', 'cx-arrow cx-arrow--intent') : null}
          {snap.hint?.to
            ? arrowEl({ from: snap.hint.from, to: snap.hint.to }, 'hint', 'cx-arrow cx-arrow--hint')
            : null}
        </svg>

        {snap.disambiguation.map((d) => {
          const { col, row } = pos(d.from, flipped);
          return (
            <span
              key={`d${d.label}${d.from}`}
              className="cx-badge"
              style={{ left: `${col * 12.5 + 6.25}%`, top: `${row * 12.5 + 6.25}%` }}
            >
              {d.label}
            </span>
          );
        })}

        {Object.entries(snap.badges).map(([ply, label]) => {
          if (Number(ply) !== snap.moves.length || !snap.last_move) return null;
          const { col, row } = pos(snap.last_move.to, flipped);
          return (
            <span
              key={`q${ply}`}
              className={`cx-quality cx-quality--${label.toLowerCase()}`}
              style={{ left: `${col * 12.5 + 8.4}%`, top: `${row * 12.5 - 1.6}%` }}
            >
              {label[0]}
            </span>
          );
        })}

        {promo ? (
          <div className="cx-promo" onPointerDown={(e) => e.stopPropagation()}>
            {PROMO.map((t, i) => {
              const { col, row } = pos(promo.to, flipped);
              const a = (-90 + (i - 1.5) * 52) * (Math.PI / 180);
              const dir = row < 4 ? 1 : -1;
              const dx = Math.cos(a) * 2.6 * 12.5;
              const dy = Math.sin(a) * 2.6 * 12.5 * -dir * -1;
              return (
                <button
                  key={t}
                  type="button"
                  className="cx-promo__opt"
                  style={{
                    left: `${col * 12.5 + 6.25 + dx}%`,
                    top: `${row * 12.5 + 6.25 + (dir === 1 ? Math.abs(dy) : -Math.abs(dy))}%`,
                    animationDelay: `${i * 0.04}s`,
                  }}
                  onClick={() => {
                    onMove(promo.from, promo.to, t);
                    setPromo(null);
                    setSelected(null);
                  }}
                >
                  <PieceArt type={t} className="cx-piece__art" title={`promote to ${t}`} />
                </button>
              );
            })}
            <button type="button" className="cx-promo__cancel" onClick={() => setPromo(null)}>
              ×
            </button>
          </div>
        ) : null}

        <EffectsCanvas fx={fx} reduced={reduced} />
      </div>

      {drag?.moved
        ? (() => {
            const p = pieces.find((x) => x.id === drag.id);
            if (!p) return null;
            return (
              <div
                className={`cx-ghost cx-piece--${p.colour}`}
                style={{ left: drag.x, top: drag.y }}
              >
                <PieceArt type={p.type} className="cx-piece__art" />
              </div>
            );
          })()
        : null}
    </div>
  );
}
