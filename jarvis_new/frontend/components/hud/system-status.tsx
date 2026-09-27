'use client';

import { useDisplayMode } from '@/hooks/hud/use-display-mode';
import { useJarvisState } from '@/hooks/hud/use-jarvis-state';

export function SystemStatus({ profile = 'Sir' }: { profile?: string }) {
  const { label, color, jarvis } = useJarvisState();
  const { mode, toggle } = useDisplayMode();

  return (
    <button
      type="button"
      onClick={toggle}
      title={`Display mode: ${mode.toUpperCase()} — activate for ${mode === 'dual' ? 'SOLO' : 'DUAL'} (or press D)`}
      aria-label={`Display mode ${mode}. Activate to switch to ${mode === 'dual' ? 'solo' : 'dual'}.`}
      aria-keyshortcuts="d"
      className="hud-status"
      style={{ ['--jarvis-state' as string]: color }}
    >
      <span className="hud-status__dot" data-state={jarvis} />
      <span className="hud-status__label">{label}</span>
      <span className="hud-status__sep">·</span>
      <span className="hud-status__profile">{profile}</span>
      <span className="hud-status__sep">·</span>
      <span className="hud-status__mode">{mode.toUpperCase()}</span>
    </button>
  );
}
