'use client';

import { type CSSProperties, type ReactNode, useEffect, useState } from 'react';
import { CommandLog } from '@/components/hud/command-log';
import { EdgePulse } from '@/components/hud/edge-pulse';
import { ExecFeed } from '@/components/hud/exec-feed';
import { LiveCaption } from '@/components/hud/live-caption';
import { NodeGraph } from '@/components/hud/node-graph';
import { ParticleOrb } from '@/components/hud/particle-orb';
import { RadarSweep } from '@/components/hud/radar-sweep';
import { ResearchStrip } from '@/components/hud/research-strip';
import { SchoolReturn, useSchoolReturn } from '@/components/hud/school-return';
import { SchoolStrip } from '@/components/hud/school-strip';
import { SchoolTransition, useSchoolTransition } from '@/components/hud/school-transition';
import { StarkDials } from '@/components/hud/stark-dials';
import { StateBanner } from '@/components/hud/state-banner';
import { SysCore } from '@/components/hud/sys-core';
import { SystemStatus } from '@/components/hud/system-status';
import { VoiceDock } from '@/components/hud/voice-dock';
import { useBridgeSysSnapshot } from '@/hooks/hud/use-bridge-sys';
import { useDisplayMode } from '@/hooks/hud/use-display-mode';
import { useHudEvents } from '@/hooks/hud/use-hud-events';
import {
  JARVIS_COLORS,
  MUTED_COLOR,
  useJarvisState,
  useMicMuted,
} from '@/hooks/hud/use-jarvis-state';
import { type BridgeSys } from '@/lib/bridge';
import { hideOverlay } from '@/lib/tauri';

type Props = {
  children: ReactNode;
};

type TickSeg = { text: string; warn?: boolean };

/** Live ticker segments from bridge /sys. Null = no data yet (caller shows SYNCING…). */
function tickerSegments(sys: BridgeSys | null): TickSeg[] | null {
  if (!sys) return null;
  const phone = sys.phone ?? null;
  const onTailnet = sys.phone_tailnet?.online === true;
  const lp = sys.laptop_power ?? null;

  const suit: TickSeg = phone
    ? {
        text: `SUIT POWER ${Math.round(phone.battery)}%${
          phone.charging ? (phone.battery >= 100 ? ' ⚡ FULL' : ' ⚡ CHARGING') : ''
        }`,
        warn: !phone.charging && phone.battery < 20,
      }
    : onTailnet
      ? { text: 'SUIT POWER — APP IDLE' }
      : { text: 'SUIT POWER — NO PHONE LINK', warn: true };

  let reactor: TickSeg;
  if (!lp) {
    reactor = { text: 'ARC REACTOR —' };
  } else if (typeof lp.battery !== 'number') {
    reactor = { text: lp.ac ? 'ARC REACTOR · AC' : 'ARC REACTOR —' };
  } else {
    const watts =
      typeof lp.watts === 'number' && lp.watts > 0
        ? ` · ${Math.round(lp.watts)} W`
        : lp.ac
          ? ' · AC'
          : '';
    reactor = { text: `ARC REACTOR ${Math.round(lp.battery)}%${watts}` };
  }

  const uplink: TickSeg = phone
    ? { text: `PHONE LINKED ${Math.round(phone.age_s)}S AGO` }
    : onTailnet
      ? { text: 'PHONE ON TAILNET' }
      : { text: 'PHONE OFFLINE', warn: true };

  const grid: TickSeg = { text: sys.call_live ? 'VOICE CALL LIVE' : 'VOICE STANDBY' };

  const temp = typeof sys.cpu_temp_c === 'number' ? sys.cpu_temp_c : null;
  const core: TickSeg =
    temp === null
      ? { text: 'CORE TEMP —' }
      : { text: `CORE TEMP ${Math.round(temp)}°C`, warn: temp >= 85 };

  return [reactor, suit, uplink, grid, core];
}

/** Footer status derived from bridge /sys. Never invents numbers. */
function footStatus(sys: BridgeSys | null): { text: string; warn: boolean } {
  if (!sys) return { text: 'SYNCING…', warn: false };
  const issues: string[] = [];
  const phone = sys.phone ?? null;
  if (!phone && sys.phone_tailnet?.online !== true) issues.push('PHONE OFFLINE');
  const temp = typeof sys.cpu_temp_c === 'number' ? sys.cpu_temp_c : null;
  if (temp !== null && temp >= 85) issues.push(`CORE ${Math.round(temp)}°C`);
  const lp = sys.laptop_power ?? null;
  const batt = typeof lp?.battery === 'number' ? (lp.battery as number) : null;
  const onAc = lp?.ac === true;
  const battOk = (batt !== null && batt >= 20) || onAc;
  if (!battOk) issues.push(batt !== null ? `SUIT ${Math.round(batt)}%` : 'SUIT —');
  if (issues.length === 0) return { text: 'ALL SYSTEMS NOMINAL', warn: false };
  return { text: issues.join(' · '), warn: true };
}

/** Rolling telemetry ticker driven by bridge /sys. Same .im-ticker styling. */
function HudTicker({ sys }: { sys: BridgeSys | null }) {
  const segs = tickerSegments(sys) ?? [{ text: 'SYNCING…' }];
  return (
    <div className="im-ticker" aria-hidden="true">
      <div className="im-ticker__track">
        {[0, 1].map((copy) => (
          <span key={copy}>
            {segs.map((s, i) => (
              <span
                key={i}
                style={{
                  paddingRight: 0,
                  color: s.warn ? 'var(--im-amber)' : undefined,
                }}
              >
                {s.text}
                {'\u00A0\u00A0◆\u00A0\u00A0'}
              </span>
            ))}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Footer-right status derived from bridge /sys (amber when degraded). */
function HudFootRight({ sys }: { sys: BridgeSys | null }) {
  const foot = footStatus(sys);
  return (
    <span
      className="stark-foot__right"
      style={foot.warn ? { color: 'var(--im-amber)' } : undefined}
    >
      {foot.text}
    </span>
  );
}

/**
 * Voice-first JARVIS shell. Solo collapses to orb + banner + voice dock
 * + caption + activity log; dual mode adds workflow and system instruments.
 *
 * Hands-free keys: `M` toggles the mic, `D` flips dual/solo, `Esc` hides.
 * NumpadEnter summons the voice session from VoiceDock.
 */
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
  const [tall, setTall] = useState(true);
  useEffect(() => {
    const onSignal = (e: Event) =>
      setSignal({ phase: String((e as CustomEvent).detail), at: Date.now() });
    const check = () => setTall(window.innerHeight >= HUD_MIN_H);
    check();
    window.addEventListener('jarvis-school', onSignal);
    window.addEventListener('resize', check);
    const timer = setInterval(check, 150); // WebKitGTK resize events lag
    return () => {
      window.removeEventListener('jarvis-school', onSignal);
      window.removeEventListener('resize', check);
      clearInterval(timer);
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
  }, [unfolding]);

  return { bar: (school && !tall) || leaving, leaving };
}

/** True while the shell has shrunk the window to the Brave taskbar orb
 *  (shell/src-tauri/src/orb.rs). Size-based, so it flips the instant the
 *  window resizes instead of waiting for the next bridge poll. */
function useTinyWindow(): boolean {
  const [tiny, setTiny] = useState(false);
  useEffect(() => {
    const check = () => setTiny(window.innerWidth < 120 && window.innerHeight < 120);
    check();
    window.addEventListener('resize', check);
    // WebKitGTK fires no resize when the shell shrinks the window while it
    // is hidden (orb.rs resizes, then shows): poll as a backstop.
    const timer = setInterval(check, 1000);
    return () => {
      window.removeEventListener('resize', check);
      clearInterval(timer);
    };
  }, []);
  return tiny;
}

/** The Brave taskbar orb: a small glowing dot in Jarvis's state colour.
 *  (The particle orb is drawn for the full HUD; at 40 px only a corner of
 *  its ring showed.) Greys out when muted, pulses faster while he talks. */
function TaskbarOrb() {
  const { color: stateColor, jarvis } = useJarvisState();
  const { muted } = useMicMuted();
  const color = muted === true ? MUTED_COLOR : stateColor;
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
  const { events } = useHudEvents();
  const { jarvis } = useJarvisState();
  const { muted, toggle: toggleMute } = useMicMuted();
  const sys = useBridgeSysSnapshot();
  const tiny = useTinyWindow();
  const school = useSchoolView(sys?.mode);
  const { tx, barH, showBar, done } = useSchoolTransition();
  const { ret, retStage, setRetStage, barPx: retBarPx, done: retDone } = useSchoolReturn();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
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
  }, [toggleMode, toggleMute]);

  const returning = ret !== null;
  const pooling = tx !== null && barH !== null;
  const hudView = isSolo ? (
    <div className="hud hud--solo" data-state={jarvis}>
      <div className="hud-ambient" aria-hidden="true" />
      <div className="im-boot" aria-hidden="true" />
      <EdgePulse events={events} solo />
      <div className="hud-solo__orb">
        <ParticleOrb />
      </div>
      <StateBanner compact />
      <ResearchStrip sys={sys} compact />
      <VoiceDock active={jarvis !== 'idle'} />
      <LiveCaption />
      <CommandLog fullscreen />
      {children}
    </div>
  ) : (
    <div className="hud hud--dual" data-state={jarvis}>
      <div className="hud-ambient" aria-hidden="true" />
      <div className="im-boot" aria-hidden="true" />
      <EdgePulse events={events} />
      <RadarSweep events={events} />
      <header className="hud__header" data-tauri-drag-region>
        <div className="hud__brand">
          <span className="hud__brand-mark" aria-hidden="true">
            J
          </span>
          <span className="hud__brand-copy">
            <strong>J.A.R.V.I.S.</strong>
            <small>PERSONAL INTELLIGENCE</small>
          </span>
        </div>
        <span className="hud__header-interactive" onMouseDown={(e) => e.stopPropagation()}>
          <SystemStatus />
        </span>
        <StarkDials />
        <button
          type="button"
          className="hud-hidebtn"
          title="Hide overlay — Super+J brings it back (or press Esc)"
          aria-label="Hide overlay. Super+J brings it back."
          aria-keyshortcuts="Escape"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={() => void hideOverlay()}
        >
          <span aria-hidden="true">✕</span> HIDE
        </button>
      </header>
      <StateBanner />
      <HudTicker sys={sys} />
      <ResearchStrip sys={sys} />
      <div className="hud__grid">
        <section className="hud__left" aria-label="Core interaction">
          <div className="hud__orb-wrap">
            <ParticleOrb />
          </div>
          <VoiceDock active={jarvis !== 'idle'} />
          <LiveCaption />
          <CommandLog />
        </section>
        <section className="hud__right" aria-label="Workflow and automation">
          <NodeGraph events={events} />
          <ExecFeed />
          <SysCore />
        </section>
      </div>
      <footer className="stark-foot" aria-hidden="true">
        <span className="stark-foot__brand">CODENAME // LOCKE</span>
        <span className="stark-foot__mid">DESKTOP COMMAND // LOCAL GRID</span>
        <HudFootRight sys={sys} />
      </footer>
      {children}
    </div>
  );

  if (!tx && !returning && !school.bar && tiny) {
    return <TaskbarOrb />;
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
          style={barPxNow !== null ? ({ '--sbar-h': `${barPxNow}px` } as CSSProperties) : undefined}
        >
          <SchoolStrip sys={sys} jarvis={jarvis} muted={muted} leaving={school.leaving} />
        </div>
      ) : hudOn ? (
        hudView
      ) : null}
    </>
  );
}
