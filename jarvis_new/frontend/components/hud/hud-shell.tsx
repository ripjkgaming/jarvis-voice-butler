'use client';

import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { CommandField } from '@/components/hud/command-field';
import { CommandLog } from '@/components/hud/command-log';
import { EdgePulse } from '@/components/hud/edge-pulse';
import { ExecFeed } from '@/components/hud/exec-feed';
import { LiveCaption } from '@/components/hud/live-caption';
import { NodeGraph } from '@/components/hud/node-graph';
import { ParticleOrb } from '@/components/hud/particle-orb';
import { RadarSweep } from '@/components/hud/radar-sweep';
import { SysGauges } from '@/components/hud/sys-gauges';
import { SystemStatus } from '@/components/hud/system-status';
import { useDisplayMode } from '@/hooks/hud/use-display-mode';
import { useHudEvents } from '@/hooks/hud/use-hud-events';
import { useJarvisState } from '@/hooks/hud/use-jarvis-state';
import { setOverlayClickThrough } from '@/lib/tauri';

type Props = {
  children: ReactNode;
  supportsChatInput?: boolean;
};

/**
 * 3-region JARVIS shell. Solo collapses to orb + caption only
 * (gauges hidden — orb-only rule wins). Second screen stays manual-drag.
 */
export function HudShell({ children, supportsChatInput = true }: Props) {
  const { isSolo } = useDisplayMode();
  const { events, live, intentEcho } = useHudEvents();
  const { jarvis } = useJarvisState();
  // A focused text field counts as interacting even when the orb is idle
  // (typed command while no call is active).
  const [fieldFocused, setFieldFocused] = useState(false);

  useEffect(() => {
    const onFocusIn = (e: FocusEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {
        setFieldFocused(true);
      }
    };
    const onFocusOut = () => setFieldFocused(false);
    window.addEventListener('focusin', onFocusIn);
    window.addEventListener('focusout', onFocusOut);
    return () => {
      window.removeEventListener('focusin', onFocusIn);
      window.removeEventListener('focusout', onFocusOut);
    };
  }, []);

  // Click-through idle: the always-on-top overlay forwards mouse events
  // when the orb is idle and no field is focused, so it never blocks
  // clicks to windows beneath. Any agent activity or field focus turns
  // it back off. No-op outside the Tauri shell (see lib/tauri.ts).
  useEffect(() => {
    void setOverlayClickThrough(jarvis === 'idle' && !fieldFocused);
  }, [jarvis, fieldFocused]);

  if (isSolo) {
    return (
      <div className="hud hud--solo">
        <EdgePulse events={events} solo />
        <div className="hud-solo__orb">
          <ParticleOrb />
        </div>
        <LiveCaption />
        <CommandLog fullscreen />
        {children}
      </div>
    );
  }

  return (
    <div className="hud hud--dual">
      <EdgePulse events={events} />
      <RadarSweep events={events} />
      <header className="hud__header">
        <SystemStatus />
        <SysGauges />
      </header>
      <div className="hud__grid">
        <section className="hud__left" aria-label="Core interaction">
          <div className="hud__orb-wrap">
            <ParticleOrb />
          </div>
          <CommandField supportsChatInput={supportsChatInput} ghostHint={intentEcho} />
          <LiveCaption />
          <CommandLog />
        </section>
        <section className="hud__right" aria-label="Workflow and automation">
          <NodeGraph events={events} />
          <ExecFeed live={live} />
        </section>
      </div>
      {children}
    </div>
  );
}
