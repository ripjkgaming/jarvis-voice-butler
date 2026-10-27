/** Wire types for /characters and /character (src/characters.py, switch_state.py). */

/** SwitchMachine phase values as sent by the bridge (lowercase). */
export type Phase =
  | 'idle'
  | 'preparing'
  | 'exiting'
  | 'signature'
  | 'entering'
  | 'settling'
  | 'rollback';

export type CharacterPublic = {
  id: string;
  display_name: string;
  spoken_name: string;
  address: string;
  lines: Record<string, string[]>;
  theme_tokens: Record<string, string | number>;
  wake_names: string[];
  layout: string;
  signature: string;
  intensity: number;
};

export type Range = [number, number];

/** characters/_motion.yaml. Every duration and easing the UI uses. */
export type LayoutSpec = {
  flow: 'columns' | 'cards';
  columns?: [number, number, number];
  cards?: number;
  top_h: number;
  gap: number;
  reactor_scale: number;
  summary: boolean;
};

export type Motion = {
  total_ms: number;
  beat_ms: number;
  reduced_ms: number;
  ready_gate_ms: number;
  fast_forward_ms: number;
  max_flash_hz: number;
  max_shake_px: number;
  max_glitch_alpha: number;
  easing: Record<string, [number, number, number, number]>;
  phases: Record<string, Range | number>;
  signatures: Record<string, Record<string, Range>>;
  particles?: {
    cap_desktop: number;
    cap_low: number;
    low_cores: number;
    retint_ms: number;
    size_steps: number;
  };
  glow?: {
    buffer_scale: number;
    budget_ms: number;
    budget_frames: number;
    env_kill: string;
    setting_kill: string;
  };
  layouts?: Record<string, LayoutSpec>;
};

export type Snapshot = {
  active: string;
  target: string | null;
  phase: Phase;
  char_turn: number;
};

export type CharactersPayload = {
  ok?: boolean;
  active: string;
  motion: Motion;
  characters: Record<string, CharacterPublic>;
};
