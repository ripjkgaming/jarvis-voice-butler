'use client';

import { useJarvisState, useMicMuted } from '@/hooks/hud/use-jarvis-state';

/**
 * Ambient voice-state banner: one glanceable line for hands-free use.
 * Big state dot + status word + mic posture + keyboard hints. The shell
 * also mirrors `data-state` on `.hud` so the whole surface tints cheaply
 * via CSS (no extra canvas, no WebGL).
 */
export function StateBanner({ compact = false }: { compact?: boolean }) {
  const { label, color, jarvis } = useJarvisState();
  const { muted } = useMicMuted();
  const micLabel = muted === true ? 'MUTED' : muted === false ? 'MIC LIVE' : 'MIC …';

  return (
    <div
      className="hud-state"
      data-state={jarvis}
      data-compact={compact ? 'true' : 'false'}
      role="status"
      aria-live="polite"
      aria-label={`${label}, microphone ${micLabel.toLowerCase()}`}
      style={{ ['--jarvis-state' as string]: color }}
    >
      <span className="hud-state__dot" data-state={jarvis} aria-hidden="true" />
      <span className="hud-state__label">{label}</span>
      <span
        className="hud-state__mic"
        data-muted={muted === true ? 'true' : 'false'}
        title={muted === true ? 'Microphone muted — hotword + call silent' : 'Microphone live'}
      >
        {micLabel}
      </span>
    </div>
  );
}
