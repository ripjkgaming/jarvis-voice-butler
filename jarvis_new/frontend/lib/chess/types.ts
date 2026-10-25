/** Wire types for the games server (src/games/server.py, ws://127.0.0.1:4320). */

export type Colour = 'white' | 'black';
export type PieceType = 'p' | 'n' | 'b' | 'r' | 'q' | 'k';

export interface EvalInfo {
  cp: number | null;
  mate: number | null;
  best?: string | null;
  depth?: number;
}

export interface ClockInfo {
  timed: boolean;
  white: number;
  black: number;
  paused: boolean;
  running: boolean;
}

export interface Flags {
  analysis: boolean;
  commentary: boolean;
  eval: boolean;
  hints: boolean;
  blunders: boolean;
  time: boolean;
}

export interface ChessSettings {
  difficulty: string;
  colour: string;
  time_control: string;
  confirm_moves: boolean;
  announce_moves: boolean;
  stop_ack: string;
  remember_analysis: boolean;
  commentary_volume: number;
  commentary_muted: boolean;
  takeover_strength: string;
  hint_default: number;
  detail: string;
  audience: string;
  eval_bar: boolean;
  square_labels: boolean;
  reduce_animations: boolean;
  board_size: number;
  piece_colours: string;
  board_theme: string;
  intro: boolean;
  intro_length: string;
  models: string[];
  online_explanations: boolean;
  speak_explanations: boolean;
  whisper_model: string;
  device: string;
  end_silence_ms: number;
  mic_device: string | null;
  speaker_device: string | null;
  piper_length_scale: number;
  echo_gate: boolean;
  require_prefix: boolean;
  paused_clock_during_jarvis: boolean;
}

export interface Panel {
  headline?: string;
  detail?: string;
  spoken?: string;
  source?: string;
  model?: string | null;
  ply?: number;
  pinned?: boolean;
}

export interface Hint {
  level: number;
  fen: string;
  from: string;
  to: string | null;
  text: string;
}

export interface ReviewMoment {
  ply: number;
  san: string;
  label: string;
  cp_loss: number;
  human: boolean;
  fen: string;
  text: string;
  move_no: number;
}

export interface Review {
  active: boolean;
  accuracy: number;
  mistakes: number;
  moments: ReviewMoment[];
  graph: number[];
  index: number;
  fen?: string;
  ply?: number;
}

export interface Flash {
  id: number;
  squares: string[];
  kind: string;
  ui?: { cmd: string; [k: string]: unknown };
}

export interface Snapshot {
  game: 'chess';
  ui_mode: 'classic' | 'voice';
  fen: string;
  turn: Colour;
  human_colour: Colour;
  mode: 'play' | 'takeover' | 'spectate';
  last_move: { from: string; to: string; uci: string } | null;
  check: string | null;
  moves: string[];
  uci_moves: string[];
  captured: { white: string[]; black: string[] };
  material: { white: number; black: number; diff: number };
  clock: ClockInfo;
  result: { outcome: string; reason: string } | null;
  can_undo: boolean;
  can_redo: boolean;
  legal: string[];
  status: string;
  analysis_on: boolean;
  flags: Flags;
  eval: EvalInfo | null;
  badges: Record<string, string>;
  caption: string;
  heard: string;
  heard_squares: string[];
  listening: boolean;
  speaking: boolean;
  mic_state: string;
  jarvis_active: boolean;
  banner: string | null;
  paused: boolean;
  pending: { type: string } | null;
  disambiguation: { label: number; from: string; to: string }[];
  thinking: boolean;
  arrow: { from: string; to: string; id: number } | null;
  highlights: string[];
  flash: Flash | null;
  hint: Hint | null;
  panel: Panel | null;
  review: Review | null;
  spectate: { speed: number; paused: boolean };
  takeover_remaining: number | null;
  flipped: boolean;
  settings: ChessSettings;
  engine: string | null;
  ai_left: number;
  ai_online: boolean;
  last_say: string;
  preset: string;
  saves: string[];
}

export interface Reply {
  type: 'reply';
  id: number;
  ok: boolean;
  say: string | null;
  text: string | null;
  kind: string;
  data: Record<string, unknown>;
}
