'use client';

import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { CharacterChip } from '@/components/character/character-chip';
import { ChatDock } from '@/components/character/chat-dock';
import { AmbientParticles } from '@/components/hud/ambient-particles';
import { VoiceLinkRail } from '@/components/hud/voice-link';
import { JARVIS_COLORS, type JarvisState, MUTED_COLOR } from '@/hooks/hud/use-jarvis-state';
import { type BridgeSys } from '@/lib/bridge';
import { skinStateColor, taglineOf, useSkin, wordmarkOf } from '@/lib/character/hooks';
import { type VoiceLink } from '@/lib/voice-link';
import { Desk } from './desk';
import { Frame } from './frame';
import { Links, Power, Trends } from './instruments';
import { type Sample, healthLine, metricsFrom, pushSample } from './metrics';
import { CommsLog, Execution, VoicePanels, useActivity } from './ops';
import { Reactor } from './reactor';
import s from './stark.module.css';
import { Caption, Research, StateReadout } from './voice';

function two(n: number): string {
  return String(n).padStart(2, '0');
}

/** Clock + date. Null until mounted (the static export would otherwise
 *  hydrate with the build machine's time). Ticks only while visible. */
function Clock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => {
      if (!document.hidden) setNow(new Date());
    }, 1000);
    return () => clearInterval(t);
  }, []);
  const time = now ? `${two(now.getHours())}:${two(now.getMinutes())}` : '--:--';
  const sec = now ? two(now.getSeconds()) : '--';
  const date = now
    ? now
        .toLocaleDateString('en-GB', {
          weekday: 'short',
          day: '2-digit',
          month: 'short',
          year: 'numeric',
        })
        .toUpperCase()
        .replace(/,/g, '')
    : '';
  return (
    <div className={s.clock} aria-label="Local time">
      <span className={s.clockTime}>
        {time}
        <span className={s.clockSec}>{sec}</span>
      </span>
      <span className={s.clockDate}>{date}</span>
    </div>
  );
}

/** Glyph emblem: the reactor triangle in a box-drawn cell. */
function Emblem() {
  return (
    <span className={s.emblem} aria-hidden="true">
      <span>╭───╮</span>
      <span>
        │<b>◢◣</b>│
      </span>
      <span>╰───╯</span>
    </span>
  );
}

const STATE_CHIP: Record<JarvisState, string> = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'PROCESSING',
  speaking: 'SPEAKING',
};

function TopBar({
  jarvis,
  muted,
  solo,
  link,
}: {
  jarvis: JarvisState;
  muted: boolean | null;
  solo: boolean;
  link: VoiceLink;
}) {
  const { ch } = useSkin();
  return (
    <header className={s.top} data-tauri-drag-region>
      <div className={s.brand} data-tauri-drag-region>
        <Emblem />
        <div className={s.brandCopy} data-tauri-drag-region>
          <strong className={s.wordmark}>{ch ? wordmarkOf(ch) : 'J.A.R.V.I.S.'}</strong>
          <small className={s.brandSub}>
            {ch ? taglineOf(ch) : 'LOCKE OS · PERSONAL INTELLIGENCE'}
          </small>
        </div>
      </div>
      <div className={s.chips} data-tauri-drag-region>
        <span className={s.chip} data-tone="state">
          <i className={s.chipDot} aria-hidden="true">
            ■
          </i>
          {muted === true ? 'MUTED' : link.joining ? 'LINKING' : STATE_CHIP[jarvis]}
        </span>
        <span className={s.chip} data-tone={muted === true ? 'warn' : 'ok'}>
          MIC {muted === true ? 'OFF' : muted === false ? 'ON' : '…'}
        </span>
        <span className={s.chip}>{solo ? 'SOLO' : 'DUAL'} · D</span>
        <CharacterChip />
      </div>
      <Clock />
    </header>
  );
}

/** Short status cards for the glass layout. Always rendered (content is never
 *  skin-dependent); the stylesheet shows it only where the layout asks. */
function Summary({
  m,
  jarvis,
  muted,
  activity,
}: {
  m: ReturnType<typeof metricsFrom>;
  jarvis: JarvisState;
  muted: boolean | null;
  activity: ReturnType<typeof useActivity>;
}) {
  const running = activity.filter((a) => a.status === 'running').length;
  const done = activity.filter((a) => a.status === 'done').length;
  const health = healthLine(m);
  const cards: [string, string, string | undefined][] = [
    ['STATUS', muted === true ? 'MUTED' : STATE_CHIP[jarvis], undefined],
    [
      'TASKS',
      running ? `${running} RUNNING` : done ? `${done} DONE` : 'ALL CLEAR',
      running ? 'live' : undefined,
    ],
    [
      'SYSTEM',
      m.has ? `CPU ${Math.round(m.cpuPct)}% · MEM ${Math.round(m.memPct)}%` : 'SYNCING',
      undefined,
    ],
    ['HEALTH', health.text, health.warn ? 'warn' : undefined],
  ];
  return (
    <section className={s.summary} aria-label="Summary" data-summary="true">
      {cards.map(([k, v, tone]) => (
        <div key={k} className={s.summaryCard} data-tone={tone}>
          <small>{k}</small>
          <strong>{v}</strong>
        </div>
      ))}
    </section>
  );
}

function Footer({ m }: { m: ReturnType<typeof metricsFrom> }) {
  const health = healthLine(m);
  if (health.text !== 'ALL SYSTEMS NOMINAL') return null;
  return (
    <footer className={s.foot}>
      <span className={s.footHealth} data-warn={health.warn ? 'true' : undefined}>
        <i aria-hidden="true">■</i>
        ALL SYSTEMS NOMINAL
      </span>
    </footer>
  );
}

/** Sparkline history kept across re-renders: one sample per new /sys. */
function useHistory(m: ReturnType<typeof metricsFrom>, sys: BridgeSys | null): Sample[] {
  const [history, setHistory] = useState<Sample[]>([]);
  const last = useRef<BridgeSys | null>(null);
  useEffect(() => {
    if (!sys || sys === last.current) return;
    last.current = sys;
    setHistory((h) => pushSample(h, m));
  }, [sys, m]);
  return history;
}

/** A finished activity flashes the frame edge once (task-done ack). */
function useDoneFlash(items: ReturnType<typeof useActivity>): boolean {
  const seen = useRef<Set<string> | null>(null);
  const [flash, setFlash] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    []
  );
  useEffect(() => {
    const done = new Set(items.filter((a) => a.status === 'done').map((a) => a.id));
    const before = seen.current;
    seen.current = done;
    if (!before) return;
    if ([...done].some((id) => !before.has(id))) {
      setFlash(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setFlash(false), 1600);
    }
  }, [items]);
  return flash;
}

/**
 * LOCKE OS HUD (design/LOCKE_OS.md). Three columns around the arc
 * reactor: vitals and links on the left, execution, voice panels, the
 * comms log and the desk (AI usage, paper market, drafts) on the right. Solo mode keeps only the reactor column.
 *
 * Keeps the `hud` class so the school-mode transition can clone it, but
 * reports its state on `data-jstate`: the legacy `.hud[data-state]` rules
 * (which freeze every animation) never match this tree.
 */
export function StarkHud({
  sys,
  solo,
  jarvis,
  muted,
  children,
  link,
}: {
  sys: BridgeSys | null;
  solo: boolean;
  jarvis: JarvisState;
  muted: boolean | null;
  children?: ReactNode;
  link: VoiceLink;
}) {
  const m = useMemo(() => metricsFrom(sys), [sys]);
  const history = useHistory(m, sys);
  const activity = useActivity();
  const flash = useDoneFlash(activity);
  const { ch: skin } = useSkin();
  const color =
    muted === true ? MUTED_COLOR : skin ? skinStateColor(skin, jarvis) : JARVIS_COLORS[jarvis];

  const center = (
    <section className={s.center} aria-label="Voice core">
      <div className={s.reactorWrap}>
        <Reactor m={m} link={link} color={color} jarvis={jarvis} muted={muted === true} />
      </div>
      <StateReadout jarvis={jarvis} muted={muted} link={link} />
      <VoiceLinkRail link={link} />
      <Caption />
      <Research sys={sys} />
    </section>
  );

  return (
    <div
      className={`hud ${s.root}`}
      data-jstate={jarvis}
      data-voice-phase={link.phase}
      data-muted={muted === true ? 'true' : undefined}
      data-solo={solo ? 'true' : undefined}
      data-flash={flash ? 'true' : undefined}
      style={{ '--state': color } as CSSProperties}
    >
      <div className={s.backdrop} aria-hidden="true" />
      <AmbientParticles
        variant="hud"
        active={!link.joining}
        state={muted === true ? 'muted' : jarvis}
        className={s.ambient}
        anchorSelector={`.${s.reactor}`}
      />
      <TopBar jarvis={jarvis} muted={muted} solo={solo} link={link} />
      {solo ? (
        <main className={s.soloGrid}>
          {center}
          <CommsLog index={1} />
        </main>
      ) : (
        <main className={s.grid}>
          <Summary m={m} jarvis={jarvis} muted={muted} activity={activity} />
          <aside className={s.left} aria-label="Vitals">
            <Frame label="VITALS" index={1} aside="2 MIN">
              <Trends m={m} history={history} />
            </Frame>
            <Frame label="POWER" index={2}>
              <Power m={m} />
            </Frame>
            <Frame label="LINKS" index={3} className={s.grow}>
              <Links m={m} />
            </Frame>
          </aside>
          {center}
          <aside className={s.right} aria-label="Operations">
            <Execution items={activity} index={4} />
            <VoicePanels index={5} />
            <CommsLog index={6} compact />
            <Desk index={7} />
          </aside>
        </main>
      )}
      <Footer m={m} />
      <i className={s.flashEdge} aria-hidden="true" />
      <ChatDock />
      {children}
    </div>
  );
}
