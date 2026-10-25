import type { Colour, PieceType } from './types';

/** A piece with a stable identity, so moves animate instead of re-rendering. */
export interface PieceInst {
  id: number;
  type: PieceType;
  colour: Colour;
  sq: string;
}

export interface MoveEvent {
  uci: string;
  from: string;
  to: string;
  piece: PieceType;
  colour: Colour;
  captured: PieceInst | null;
  castle: boolean;
  promotion: PieceType | null;
  /** Animation key: changes on every distinct move. */
  seq: number;
}

export const FILES = 'abcdefgh';

export function sqName(file: number, rank: number): string {
  return FILES[file] + String(rank + 1);
}

export function sqCoords(sq: string): { file: number; rank: number } {
  return { file: FILES.indexOf(sq[0]), rank: Number(sq[1]) - 1 };
}

let nextId = 1;

export function fromFen(fen: string): PieceInst[] {
  const out: PieceInst[] = [];
  const rows = fen.split(' ')[0].split('/');
  rows.forEach((row, i) => {
    let file = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) {
        file += Number(ch);
      } else {
        const colour: Colour = ch === ch.toUpperCase() ? 'white' : 'black';
        out.push({
          id: nextId++,
          type: ch.toLowerCase() as PieceType,
          colour,
          sq: sqName(file, 7 - i),
        });
        file += 1;
      }
    }
  });
  return out;
}

/** Apply one UCI move to a piece list (castling, en passant, promotion). */
export function applyMove(
  pieces: PieceInst[],
  uci: string,
  seq: number
): { pieces: PieceInst[]; event: MoveEvent | null } {
  const from = uci.slice(0, 2);
  const to = uci.slice(2, 4);
  const promo = (uci[4] as PieceType | undefined) ?? null;
  const mover = pieces.find((p) => p.sq === from);
  if (!mover) return { pieces, event: null };
  let captured: PieceInst | null =
    pieces.find((p) => p.sq === to && p.colour !== mover.colour) ?? null;
  const f = sqCoords(from);
  const t = sqCoords(to);
  if (!captured && mover.type === 'p' && f.file !== t.file) {
    captured = pieces.find((p) => p.sq === sqName(t.file, f.rank) && p.type === 'p') ?? null;
  }
  const castle = mover.type === 'k' && Math.abs(t.file - f.file) === 2;
  const next = pieces
    .filter((p) => p !== captured)
    .map((p) => {
      if (p === mover) return { ...p, sq: to, type: promo ?? p.type };
      if (castle && p.type === 'r' && p.colour === mover.colour && p.sq[1] === from[1]) {
        const kingside = t.file > f.file;
        const rookFrom = sqName(kingside ? 7 : 0, f.rank);
        if (p.sq === rookFrom) return { ...p, sq: sqName(kingside ? 5 : 3, f.rank) };
      }
      return p;
    });
  return {
    pieces: next,
    event: {
      uci,
      from,
      to,
      piece: mover.type,
      colour: mover.colour,
      captured,
      castle,
      promotion: promo,
      seq,
    },
  };
}

export interface Tracked {
  moves: string[];
  pieces: PieceInst[];
  event: MoveEvent | null;
  /** Bumps when the position was rebuilt from scratch (new game, load, undo). */
  epoch: number;
}

/** Follow the game move by move so pieces keep their identity. */
export function syncPieces(prev: Tracked | null, fen: string, moves: string[]): Tracked {
  if (!prev) {
    return { moves, pieces: fromFen(fen), event: null, epoch: 0 };
  }
  if (moves.length === prev.moves.length + 1 && prev.moves.every((m, i) => moves[i] === m)) {
    const { pieces, event } = applyMove(prev.pieces, moves[moves.length - 1], moves.length);
    if (event) return { moves, pieces, event, epoch: prev.epoch };
  }
  if (moves.length === prev.moves.length && prev.moves.every((m, i) => moves[i] === m)) {
    return { ...prev, moves };
  }
  return { moves, pieces: fromFen(fen), event: null, epoch: prev.epoch + 1 };
}

export const PIECE_NAMES: Record<PieceType, string> = {
  p: 'pawn',
  n: 'knight',
  b: 'bishop',
  r: 'rook',
  q: 'queen',
  k: 'king',
};

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}

/** Legal destinations per origin square from the server's UCI list. */
export function legalMap(legal: string[]): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const u of legal) {
    const from = u.slice(0, 2);
    const to = u.slice(2, 4);
    const list = map.get(from) ?? [];
    if (!list.includes(to)) list.push(to);
    map.set(from, list);
  }
  return map;
}

export function isPromotion(legal: string[], from: string, to: string): boolean {
  return legal.some((u) => u.slice(0, 4) === from + to && u.length === 5);
}

/** Win-probability ring value in [0, 1] (white's view) from centipawns / mate. */
export function evalToUnit(cp: number | null, mate: number | null): number {
  if (mate !== null && mate !== undefined) return mate > 0 ? 1 : 0;
  const v = Math.max(-1500, Math.min(1500, cp ?? 0));
  return 1 / (1 + Math.exp(-0.00368208 * v));
}

export function evalLabel(cp: number | null, mate: number | null): string {
  if (mate !== null && mate !== undefined) return `M${Math.abs(mate)}`;
  const v = (cp ?? 0) / 100;
  return (v > 0 ? '+' : '') + v.toFixed(1);
}

/** Join class names; falsy entries drop out (no leading-space tricks). */
export function cls(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}
