'use client';

import { type CSSProperties, type ReactNode, useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import { SchoolReturn, useSchoolReturn } from '@/components/hud/school-return';
import { SchoolStrip } from '@/components/hud/school-strip';
import { SchoolTransition, useSchoolTransition } from '@/components/hud/school-transition';
import { StarkHud } from '@/components/stark/stark-hud';
import { useBridgeSysSnapshot } from '@/hooks/hud/use-bridge-sys';
import { useDisplayMode } from '@/hooks/hud/use-display-mode';
import { useInsights } from '@/hooks/hud/use-insights';
import {
  JARVIS_COLORS,
  MUTED_COLOR,
  useJarvisState,
  useMicMuted,
} from '@/hooks/hud/use-jarvis-state';
import { useSuitDiagnostics } from '@/hooks/hud/use-suit-diagnostics';
import { useVoiceLink } from '@/hooks/hud/use-voice-link';
import { useWindowGeometry } from '@/hooks/hud/use-window-geometry';
import { hideOverlay } from '@/lib/tauri';

type Props = {
  children: ReactNode;
};

const SuitDiagnostics = dynamic(() => import('@/components/suit/suit-diagnostics'), { ssr: false });
const InsightsDrawer = dynamic(() => import('@/components/insights/insights-drawer'), {
  ssr: false,
});

/** Pause every CSS animation while the window is hidden/minimised. */
function useHiddenPause(): void {
  useEffect(() => {
    const root = document.documentElement;
    const sync = () => root.classList.toggle('hud-hidden', document.hidden);
    sync();
    document.addEventListener('visibilitychange', sync);
    return () => {
      document.removeEventListener('visibilitychange', sync);
      root.classList.remove('hud-hidden');
    };
  }, []);
}

/** The HUD's minimum height (tauri.conf.json); anything shorter is the
 *  docked school taskbar, even with a menu grown out of it. */
const HUD_MIN_H = 540;
/** How long a shell transition signal outranks the bridge's mode poll
 *  (covers the whole entry transition, ~4-5 s). */
const SIGNAL_TRUST_MS = 12000;

/** Which face to show around a school-mode transition. The shell sends a
 *  'jarvis-school' DOM event (school.rs) *before* it moves the window:
 *  "collapse"/"arrive" = the entry transition (school-transition.tsx)
 *  plays, then the bar docks; "expand" = close the bar into its line, then
 *  the HUD comes back and unfolds upward. Between signals the bridge's
 *  mode is the source of truth. */
function useSchoolView(mode: string | undefined): { bar: boolean; leaving: boolean } {
  const [signal, setSignal] = useState<{ phase: string; at: number } | null>(null);
  const { width, height } = useWindowGeometry();
  const tall = height >= HUD_MIN_H;
  // Once docked, a menu can grow to >= HUD_MIN_H without changing face.
  const [docked, setDocked] = useState(false);
  useEffect(() => {
    if (height <= 140 && width >= 120) setDocked(true);
  }, [width, height]);
  useEffect(() => {
    let expiry: ReturnType<typeof setTimeout> | undefined;
    const onSignal = (e: Event) => {
      const phase = String((e as CustomEvent).detail);
      setSignal({ phase, at: Date.now() });
      // Clear stale dock geometry, including a hidden/orb return whose
      // shell restore did not emit an expand signal.
      setDocked(false);
      clearTimeout(expiry);
      expiry = setTimeout(() => setSignal(null), SIGNAL_TRUST_MS);
    };
    window.addEventListener('jarvis-school', onSignal);
    return () => {
      window.removeEventListener('jarvis-school', onSignal);
      clearTimeout(expiry);
    };
  }, []);

  const fresh = signal && Date.now() - signal.at < SIGNAL_TRUST_MS ? signal.phase : null;
  const school = fresh ? fresh === 'collapse' || fresh === 'arrive' : mode === 'school';
  const leaving = fresh === 'expand' && !tall;

  // Unfold the HUD once, the moment it is tall again after an expand.
  const [unfolding, setUnfolding] = useState(false);
  useEffect(() => {
    if (fresh !== 'expand' || !tall) return;
    setUnfolding(true);
    const t = setTimeout(() => setUnfolding(false), 850);
    return () => clearTimeout(t);
  }, [fresh, tall]);

  useEffect(() => {
    document.documentElement.classList.toggle('hud-unfolding', unfolding);
    return () => document.documentElement.classList.remove('hud-unfolding');
  }, [unfolding]);

  return { bar: (school && (!tall || docked)) || leaving, leaving };
}

/** True while the shell has shrunk the window to the Brave taskbar orb
 *  (shell/src-tauri/src/orb.rs). Size-based, so it flips the instant the
 *  window resizes instead of waiting for the next bridge poll. */
function useTinyWindow(): boolean {
  const { width, height } = useWindowGeometry();
  return width < 120 && height < 120;
}

/** The Brave taskbar orb: a small glowing dot in Jarvis's state colour.
 *  (The particle orb is drawn for the full HUD; at 40 px only a corner of
 *  its ring showed.) Greys out when muted, pulses faster while he talks. */
function TaskbarOrb({
  jarvis,
  muted,
}: {
  jarvis: ReturnType<typeof useJarvisState>['jarvis'];
  muted: boolean | null;
}) {
  const color = muted === true ? MUTED_COLOR : JARVIS_COLORS[jarvis];
  return (
    <div className="hud hud--orb" data-state={jarvis}>
      <span
        className="hud-orb-dot"
        data-state={jarvis}
        style={{ '--orb': color } as CSSProperties}
        aria-label={`Jarvis ${jarvis}`}
      />
    </div>
  );
}

export function HudShell({ children }: Props) {
  useHiddenPause();
  const { isSolo, toggle: toggleMode } = useDisplayMode();
  const { jarvis } = useJarvisState();
  const { muted, toggle: toggleMute } = useMicMuted();
  const link = useVoiceLink(muted);
  const sys = useBridgeSysSnapshot();
  const tiny = useTinyWindow();
  const school = useSchoolView(sys?.mode);
  const { tx, barH, barInset, showBar, done } = useSchoolTransition();
  const {
    ret,
    retStage,
    setRetStage,
    barPx: retBarPx,
    barInset: retInset,
    done: retDone,
  } = useSchoolReturn();
  const suit = useSuitDiagnostics(
    sys?.suit_diagnostics,
    school.bar,
    tx !== null || ret !== null,
    sys?.mode
  );
  const { open: suitOpen, close: closeSuit } = suit;
  const insights = useInsights(school.bar, tx !== null || ret !== null, suitOpen, suit.focusReset);
  const { visible: insightsOpen, close: closeInsights } = insights;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (insightsOpen) {
        if (e.key === 'Escape') {
          e.preventDefault();
          closeInsights();
        }
        return;
      }
      if (suitOpen) {
        // Also handle the short lazy-module load before the dialog installs
        // its own focus trap. Escape must never hide the whole shell here.
        if (e.key === 'Escape') {
          e.preventDefault();
          closeSuit();
        }
        return;
      }
      const t = e.target as HTMLElement | null;
      const typing =
        !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      if (e.key === 'Escape') {
        e.preventDefault();
        void hideOverlay();
      } else if (typing) {
        return;
      } else if (e.key === 'm' || e.key === 'M') {
        e.preventDefault();
        void toggleMute();
      } else if (e.key === 'd' || e.key === 'D') {
        e.preventDefault();
        toggleMode();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleMode, toggleMute, suitOpen, closeSuit, insightsOpen, closeInsights]);

  const returning = ret !== null;
  const pooling = tx !== null && barH !== null;
  // STARK OS HUD (components/stark): one layout for dual and solo.
  const hudView = (
    <StarkHud
      sys={sys}
      solo={isSolo}
      jarvis={jarvis}
      muted={muted}
      link={link}
      onInsights={insights.open}
    >
      {children}
    </StarkHud>
  );

  if (!tx && !returning && !school.bar && tiny) {
    return <TaskbarOrb jarvis={jarvis} muted={muted} />;
  }

  // One stable shape for every face: [transition overlay, bar | HUD]. The
  // bar keeps its place from the moment it surfaces out of the entry pool
  // until it sinks away on the return, and the HUD from its scan-in on, so
  // neither remounts (and replays its opening) mid-transition.
  const barOn = returning ? retStage === 'bar' || retStage === 'sink' : school.bar || pooling;
  const hudOn = returning ? retStage === 'scan' : !tx && !school.bar;
  const barPxNow = returning ? retBarPx : pooling ? barH : null;
  return (
    <>
      {tx ? (
        <SchoolTransition
          key={tx.id}
          tx={tx}
          color={JARVIS_COLORS[jarvis]}
          onBar={showBar}
          onDone={done}
        />
      ) : ret ? (
        <SchoolReturn
          key={ret.id}
          ret={ret}
          color={JARVIS_COLORS[jarvis]}
          barPx={retBarPx}
          barInset={retInset}
          onStage={setRetStage}
          onDone={retDone}
        />
      ) : null}
      {barOn ? (
        <div
          className="stx-bar"
          data-arrived={barH !== null || returning}
          data-pooling={pooling}
          data-returning={returning ? retStage : undefined}
          style={
            barPxNow !== null
              ? ({
                  '--sbar-h': `${barPxNow}px`,
                  '--sbar-inset': `${returning ? retInset : pooling ? barInset : 0}px`,
                } as CSSProperties)
              : undefined
          }
        >
          <SchoolStrip
            sys={sys}
            jarvis={jarvis}
            muted={muted}
            link={link}
            leaving={school.leaving}
            diagnosticsOpen={suit.open || insights.visible}
            onInsights={insights.open}
          />
        </div>
      ) : hudOn ? (
        hudView
      ) : null}
      {suit.open && (
        <SuitDiagnostics onClose={suit.close} school={school.bar} barHeight={suit.barHeight} />
      )}
      {insights.visible && (
        <InsightsDrawer
          tab={insights.tab}
          onTab={insights.open}
          onClose={insights.close}
          school={school.bar}
          barHeight={insights.barHeight}
          returnFocus={insights.returnFocus.current}
        />
      )}
    </>
  );
}
