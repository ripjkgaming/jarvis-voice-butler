'use client';

import type { ReactNode } from 'react';
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
