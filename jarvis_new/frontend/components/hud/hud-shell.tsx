'use client';

import { type ReactNode, useEffect } from 'react';
import { CommandLog } from '@/components/hud/command-log';
import { EdgePulse } from '@/components/hud/edge-pulse';
import { ExecFeed } from '@/components/hud/exec-feed';
import { LiveCaption } from '@/components/hud/live-caption';
import { NodeGraph } from '@/components/hud/node-graph';
import { ParticleOrb } from '@/components/hud/particle-orb';
import { RadarSweep } from '@/components/hud/radar-sweep';
import { ResearchStrip } from '@/components/hud/research-strip';
import { SchoolStrip } from '@/components/hud/school-strip';
import { StarkDials } from '@/components/hud/stark-dials';
import { StateBanner } from '@/components/hud/state-banner';
import { SysCore } from '@/components/hud/sys-core';
import { SystemStatus } from '@/components/hud/system-status';
import { VoiceDock } from '@/components/hud/voice-dock';
import { useBridgeSysSnapshot } from '@/hooks/hud/use-bridge-sys';
import { useDisplayMode } from '@/hooks/hud/use-display-mode';
import { useHudEvents } from '@/hooks/hud/use-hud-events';
import { useJarvisState, useMicMuted } from '@/hooks/hud/use-jarvis-state';
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

export function HudShell({ children }: Props) {
  useHiddenPause();
  const { isSolo, toggle: toggleMode } = useDisplayMode();
  const { events } = useHudEvents();
  const { jarvis } = useJarvisState();
  const { muted, toggle: toggleMute } = useMicMuted();
  const sys = useBridgeSysSnapshot();

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

  if (sys?.mode === 'school') {
    return <SchoolStrip sys={sys} jarvis={jarvis} muted={muted} />;
  }

  if (isSolo) {
    return (
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
    );
  }

  return (
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
}
