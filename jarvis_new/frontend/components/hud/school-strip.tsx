'use client';

import {
  type CSSProperties,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { BootLine } from '@/components/hud/boot-log';
import { AppIcon, MENU_EXTRA, type MenuKind, MenuLayer } from '@/components/hud/school-menus';
import { useBootLog } from '@/hooks/hud/use-boot-log';
import { JARVIS_COLORS, type JarvisState, MUTED_COLOR } from '@/hooks/hud/use-jarvis-state';
import { useCaptions, useLiveCaption, useRoom } from '@/hooks/hud/use-room-state';
import {
  type BridgeActivity,
  type BridgeSys,
  type BridgeWindow,
  bridgeLaunch,
  bridgeWindowAction,
} from '@/lib/bridge';
import { useSharedPoll } from '@/lib/shared-poll';
import { invoke, isTauri } from '@/lib/tauri';

const STATE_LABEL: Record<JarvisState, string> = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
};

/** A finished caption stays on the bar this long after it was said. */
const CAPTION_FRESH_S = 30;
/** Marquee speed for lines wider than the stream slot (px per second). */
const MARQUEE_PX_S = 42;

type Line = { who: 'Sir' | 'Jarvis'; text: string; key: string };

/** What the stream shows during a call: Jarvis's word-synced live line
 *  while he talks, else the latest fresh caption. Outside a call, nothing
 *  (the ambient trace). */
function useStreamLine(): Line | null {
  // Shared polls: call presence, then (only during a call) the live line
  // and the captions tail. One request loop each for the whole HUD.
  const inCall = useRoom(500) !== null;
  const live = useLiveCaption(inCall, 500);
  const captions = useCaptions(inCall);
  return useMemo(() => {
    if (!inCall) return null;
    if (live && !live.done && live.text.trim()) {
      return { who: 'Jarvis', text: live.text, key: `l${live.id}` };
    }
    const last = captions.at(-1);
    return last && Date.now() / 1000 - last.ts < CAPTION_FRESH_S
      ? { who: last.role === 'sir' ? 'Sir' : 'Jarvis', text: last.text, key: `c${last.ts}` }
      : null;
  }, [inCall, live, captions]);
}

/** Running system activities (downloads, research, builds), max two. */
function useRunning(): BridgeActivity[] {
  const raw = useSharedPoll<{ items?: BridgeActivity[] }>('/activity', 3000);
  return useMemo(() => (raw?.items ?? []).filter((a) => a.status === 'running').slice(0, 2), [raw]);
}

/** Docked over a floating Plasma panel? school.rs trims the panel's gap,
 *  so the window is then narrower than the screen: the bar takes the
 *  panel's rounded corners. (While it still covers the screen the pool's
 *  inset, --sbar-inset, decides; see globals.css.) */
function useFloating(): boolean {
  const [floating, setFloating] = useState(false);
  useEffect(() => {
    const check = () =>
      setFloating(window.innerHeight < 300 && window.innerWidth < window.screen.width - 4);
    check();
    window.addEventListener('resize', check);
    const t = setInterval(check, 500); // WebKitGTK resize events are unreliable
    return () => {
      window.removeEventListener('resize', check);
      clearInterval(t);
    };
  }, []);
  return floating;
}

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

const pad = (n: number) => String(n).padStart(2, '0');

/* ------------------------------ pieces ------------------------------ */

function Core() {
  return (
    <span className="sbar-core" aria-hidden="true">
      <span className="sbar-core__halo" />
      <svg className="sbar-core__ring" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="10.5" />
      </svg>
      <svg className="sbar-core__ring sbar-core__ring--inner" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="7.5" />
      </svg>
      <span className="sbar-core__dot" />
    </span>
  );
}

/** A caption line: rises in when new, glides sideways when too long. */
function StreamLine({ line }: { line: Line }) {
  const slot = useRef<HTMLSpanElement>(null);
  const text = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(0);
  useLayoutEffect(() => {
    const over = (text.current?.scrollWidth ?? 0) - (slot.current?.clientWidth ?? 0);
    setOverflow(over > 4 ? over + 24 : 0);
  }, [line.text]);
  const style = overflow
    ? ({
        '--marquee': `-${overflow}px`,
        '--marquee-s': `${Math.max(6, overflow / MARQUEE_PX_S + 3)}s`,
      } as CSSProperties)
    : undefined;
  return (
    <span className="sbar-line">
      <b className={line.who === 'Sir' ? 'is-sir' : 'is-jarvis'}>{line.who}</b>
      <span ref={slot} className="sbar-line__slot">
        <span
          ref={text}
          className={overflow ? 'sbar-line__text is-marquee' : 'sbar-line__text'}
          style={style}
        >
          {line.text}
        </span>
      </span>
    </span>
  );
}

/** How the idle signal moves per state: wave height (0-1 of the lane),
 *  drift speed, and how many ripples bloom per second. */
const TRACE_TUNE: Record<string, { amp: number; speed: number; ripples: number }> = {
  idle: { amp: 0.14, speed: 0.3, ripples: 0.35 },
  listening: { amp: 0.34, speed: 0.8, ripples: 1.4 },
  thinking: { amp: 0.22, speed: 1.9, ripples: 2.4 },
  speaking: { amp: 0.46, speed: 1.1, ripples: 1.8 },
  muted: { amp: 0.02, speed: 0.12, ripples: 0 },
};
/** Redraw cadence (ms): ~30 fps is smooth for a slow line and half the CPU. */
const TRACE_FRAME_MS = 33;

type Ripple = { x: number; w: number; amp: number; freq: number; born: number; life: number };

/** Idle stream: a live signal line. Quasi-periodic waves (irrational
 *  ratios, random phases) plus ripples that bloom at random spots and fade,
 *  so it never visibly repeats. Eases between states instead of jumping. */
function LiveTrace({ state }: { state: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const tune = useRef(TRACE_TUNE[state] ?? TRACE_TUNE.idle);
  tune.current = TRACE_TUNE[state] ?? TRACE_TUNE.idle;

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const phase = [0, 0, 0, 0].map(() => Math.random() * Math.PI * 2);
    const ripples: Ripple[] = [];
    let amp = tune.current.amp;
    let speed = tune.current.speed;
    let t = Math.random() * 1000;
    let last = performance.now();
    let color = '';
    let colorAt = -Infinity;
    let raf = 0;

    const signal = (x: number, now: number) => {
      let v =
        0.5 * Math.sin(x * 6.1 + t + phase[0]) +
        0.3 * Math.sin(x * 11.7 - t * 1.618 + phase[1]) +
        0.2 * Math.sin(x * 23.3 + t * 2.414 + phase[2]) +
        0.12 * Math.sin(x * 41.9 - t * 3.303 + phase[3]);
      v *= amp;
      for (const r of ripples) {
        const f = (now - r.born) / r.life;
        const env = Math.sin(Math.PI * f) * Math.exp(-(((x - r.x) / r.w) ** 2));
        v += r.amp * (0.2 + amp) * env * Math.sin((x - r.x) * r.freq - t * 5);
      }
      return Math.tanh(v * 1.4);
    };

    const frame = (now: number) => {
      raf = still ? 0 : requestAnimationFrame(frame);
      if (!still && (now - last < TRACE_FRAME_MS || document.hidden)) return;
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const goal = tune.current;
      const ease = Math.min(1, dt * 2.5);
      amp += (goal.amp - amp) * ease;
      speed += (goal.speed - speed) * ease;
      t += dt * speed;

      for (let i = ripples.length - 1; i >= 0; i--) {
        if (now - ripples[i].born > ripples[i].life) ripples.splice(i, 1);
      }
      if (Math.random() < goal.ripples * dt && ripples.length < 6) {
        ripples.push({
          x: 0.08 + Math.random() * 0.84,
          w: 0.03 + Math.random() * 0.07,
          amp: 0.35 + Math.random() * 0.65,
          freq: 60 + Math.random() * 120,
          born: now,
          life: 1400 + Math.random() * 2600,
        });
      }

      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      if (now - colorAt > 400) {
        color = getComputedStyle(canvas).color;
        colorAt = now;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      if (!w || !h) return;
      const mid = h / 2;
      const reach = mid - 2;
      const trace = (offset: number) => {
        ctx.beginPath();
        for (let px = 0; px <= w; px += 2) {
          const y = mid + signal(px / w + offset, now) * reach;
          if (px === 0) ctx.moveTo(px, y);
          else ctx.lineTo(px, y);
        }
        ctx.stroke();
      };
      ctx.strokeStyle = color;
      ctx.lineJoin = 'round';
      // A faint echo a little out of step, then the line itself with glow.
      ctx.globalAlpha = 0.22;
      ctx.lineWidth = 1;
      ctx.shadowBlur = 0;
      trace(0.013);
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.4;
      ctx.shadowColor = color;
      ctx.shadowBlur = 6;
      trace(0);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <span className="sbar-trace" aria-hidden="true">
      <canvas ref={ref} />
    </span>
  );
}

function Ring({ pct }: { pct: number | null }) {
  const r = 7;
  const c = 2 * Math.PI * r;
  return (
    <svg
      className={pct === null ? 'sbar-ring is-spin' : 'sbar-ring'}
      viewBox="0 0 18 18"
      aria-hidden="true"
    >
      <circle className="sbar-ring__track" cx="9" cy="9" r={r} />
      <circle
        className="sbar-ring__fill"
        cx="9"
        cy="9"
        r={r}
        strokeDasharray={c}
        strokeDashoffset={pct === null ? c * 0.7 : c * (1 - pct / 100)}
      />
    </svg>
  );
}

function Tile({
  label,
  value,
  icon,
  level,
  tone,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
  /** 0-1 fill for the micro gauge under the value. */
  level?: number | null;
  tone?: 'warn' | 'hot' | 'dim';
}) {
  return (
    <span className="sbar-tile" data-tone={tone}>
      {icon ? <span className="sbar-tile__icon">{icon}</span> : null}
      <span className="sbar-tile__body">
        <i>{label}</i>
        <b>{value}</b>
        {level !== undefined && level !== null ? (
          <span className="sbar-tile__gauge">
            <span style={{ width: `${Math.round(Math.max(0, Math.min(1, level)) * 100)}%` }} />
          </span>
        ) : null}
      </span>
    </span>
  );
}

function WifiIcon({ bars }: { bars: number }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      {[3, 6, 9].map((r, i) => (
        <path
          key={r}
          d={`M${8 - r} ${12 - r * 0.62} Q8 ${12 - r * 1.35} ${8 + r} ${12 - r * 0.62}`}
          className={i < bars ? 'on' : ''}
        />
      ))}
      <circle cx="8" cy="12.6" r="1.2" className="on dot" />
    </svg>
  );
}

function VolIcon({ muted }: { muted: boolean }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path className="on fill" d="M2 6h3l4-3v10l-4-3H2z" />
      {muted ? (
        <path className="on" d="M11 6l4 4M15 6l-4 4" />
      ) : (
        <>
          <path className="on" d="M11 5.5q1.8 2.5 0 5" />
          <path className="on" d="M12.8 3.8q3.2 4.2 0 8.4" />
        </>
      )}
    </svg>
  );
}

function BattIcon({ level, charging }: { level: number; charging: boolean }) {
  return (
    <svg viewBox="0 0 20 12" aria-hidden="true">
      <rect className="on" x="0.8" y="1.3" width="16" height="9.4" rx="2" />
      <rect className="on fill" x="17.4" y="4.2" width="1.6" height="3.6" rx="0.6" />
      <rect
        className="fill batt"
        x="2.6"
        y="3"
        width={Math.max(0.8, 12.4 * level)}
        height="6"
        rx="0.8"
      />
      {charging ? <path className="bolt" d="M10 2.4 7.2 6.4h2.4L8.6 9.6l3-4.2H9.2z" /> : null}
    </svg>
  );
}

/* ------------------------------ apps ------------------------------ */

type Launcher = { desktop: string; name: string };
type AppGroup = { key: string; app: string; label: string; pinned: boolean; wins: BridgeWindow[] };

const appKey = (id: string) => id.toLowerCase().replace(/\.desktop$/, '');

/** Plasma icon-tasks order: pinned launchers first (open windows merge into
 *  their slot, matched by desktop id or window class), then other open apps
 *  in first-seen order, one slot per app. */
function groupApps(launchers: Launcher[], windows: BridgeWindow[]): AppGroup[] {
  const groups = new Map<string, AppGroup>();
  for (const l of launchers) {
    const key = appKey(l.desktop);
    if (!groups.has(key))
      groups.set(key, { key, app: l.desktop, label: l.name, pinned: true, wins: [] });
  }
  for (const w of windows) {
    const keys = [w.desktop, w.app].filter(Boolean).map(appKey);
    const hit = keys.map((k) => groups.get(k)).find(Boolean);
    if (hit) {
      hit.wins.push(w);
      continue;
    }
    const key = keys[0] ?? w.id;
    groups.set(key, {
      key,
      app: w.desktop || w.app,
      label: w.title || w.app,
      pinned: false,
      wins: [w],
    });
  }
  return [...groups.values()];
}

/** The open apps: click to raise, click the active one to minimise, click
 *  a many-window app to cycle through its windows. The shell window never
 *  takes focus, so a click lands on the app without stealing the keyboard. */
function Apps({ launchers, windows }: { launchers: Launcher[]; windows: BridgeWindow[] }) {
  // Optimistic active id so the highlight moves on click, not next poll.
  const [pending, setPending] = useState<{ id: string | null; at: number } | null>(null);
  // Launch feedback: the icon pulses until the new window shows up.
  const [launching, setLaunching] = useState<{ key: string; at: number } | null>(null);
  const fresh = pending && Date.now() - pending.at < 2500 ? pending : null;
  const isActive = (w: BridgeWindow) => (fresh ? fresh.id === w.id : w.active);
  const groups = groupApps(launchers, windows);
  if (!groups.length) return null;

  const click = (g: AppGroup) => {
    if (!g.wins.length) {
      setLaunching({ key: g.key, at: Date.now() });
      void bridgeLaunch(g.app);
      return;
    }
    const current = g.wins.findIndex(isActive);
    if (current >= 0 && g.wins.length === 1) {
      setPending({ id: null, at: Date.now() });
      void bridgeWindowAction(g.wins[0].id, 'minimize');
      return;
    }
    const next = g.wins[(current + 1) % g.wins.length] ?? g.wins[0];
    setPending({ id: next.id, at: Date.now() });
    void bridgeWindowAction(next.id, 'activate');
  };

  return (
    <section className="sbar-apps" aria-label="Open apps">
      {groups.map((g, i) => {
        const active = g.wins.some(isActive);
        const minimized = g.wins.length > 0 && g.wins.every((w) => w.minimized) && !active;
        const title = (g.wins.find(isActive) ?? g.wins[0])?.title ?? g.label;
        const starting =
          !g.wins.length && launching?.key === g.key && Date.now() - launching.at < 8000;
        return (
          <button
            key={g.key}
            type="button"
            className="sbar-app"
            data-active={active}
            data-minimized={minimized}
            data-running={g.wins.length > 0}
            data-launching={starting}
            title={title}
            style={{ '--i': i } as CSSProperties}
            onClick={() => click(g)}
          >
            <AppIcon app={g.app} label={g.label} />
            <span className="sbar-app__pips" aria-hidden="true">
              {g.wins.slice(0, 3).map((w) => (
                <i key={w.id} data-on={isActive(w)} />
              ))}
            </span>
          </button>
        );
      })}
    </section>
  );
}

/* ------------------------------ the bar ------------------------------ */

/** School mode: Jarvis becomes the taskbar. The window is laid exactly
 *  over the bottom panel (shell/src-tauri/src/school.rs) and never takes
 *  focus. Jarvis and the open apps on the left, what he is saying in the
 *  middle, the machine on the right; the whole bar takes his state colour. */
/** Opens/closes the taskbar menus: grows the shell window upward first
 *  (school.rs `school_menu`), shows the menu once the room is there, and on
 *  close plays the menu out before handing the space back. */
function useMenu(): {
  menu: MenuKind | null;
  shown: boolean;
  closing: boolean;
  toggle: (m: MenuKind) => void;
  close: () => void;
} {
  const [menu, setMenu] = useState<MenuKind | null>(null);
  const [closing, setClosing] = useState(false);
  const [roomy, setRoomy] = useState(false);
  const barH = useRef(0);
  useEffect(() => {
    const check = () => {
      const h = window.innerHeight;
      if (h <= 140) barH.current = h;
      setRoomy(barH.current > 0 && h >= barH.current + MENU_EXTRA - 8);
    };
    check();
    window.addEventListener('resize', check);
    const t = setInterval(check, 250); // WebKitGTK resize events are unreliable
    return () => {
      window.removeEventListener('resize', check);
      clearInterval(t);
    };
  }, []);
  useEffect(() => {
    document.documentElement.style.setProperty(
      '--sbar-h',
      `${barH.current || window.innerHeight}px`
    );
  });
  const grow = (extra: number) => {
    if (isTauri()) void invoke('school_menu', { extra }).catch(() => undefined);
  };
  const close = useCallback(() => {
    setClosing(true);
    setTimeout(() => {
      setMenu(null);
      setClosing(false);
      grow(0);
    }, 170);
  }, []);
  const toggle = (m: MenuKind) => {
    if (menu === m) return close();
    setClosing(false);
    setMenu(m);
    if (!menu) grow(MENU_EXTRA);
  };
  return { menu, shown: !!menu && roomy, closing, toggle, close };
}

export function SchoolStrip({
  sys,
  jarvis,
  muted,
  leaving = false,
}: {
  sys: BridgeSys | null;
  jarvis: JarvisState;
  muted: boolean | null;
  /** School mode is ending: fold the bar back into its line. */
  leaving?: boolean;
}) {
  const line = useStreamLine();
  const boot = useBootLog();
  const running = useRunning();
  const now = useNow();
  const floating = useFloating();
  const { menu, shown, closing, toggle, close } = useMenu();
  const state = muted ? 'muted' : jarvis;
  const color = muted ? MUTED_COLOR : JARVIS_COLORS[jarvis];

  const research = sys?.research ?? [];
  const jobs = [
    ...research.map((r) => ({
      id: r.id,
      title: r.title,
      pct: Math.round(r.progress) as number | null,
    })),
    ...running
      .filter((a) => a.kind !== 'research')
      .map((a) => ({
        id: a.id,
        title: a.title,
        pct: a.progress === null ? null : Math.round(a.progress),
      })),
  ].slice(0, 2);

  const power = sys?.laptop_power ?? null;
  const batt = power?.battery ?? null;
  const charging =
    !!power && (power.ac === true || (/charg/i.test(power.status) && !/dis/i.test(power.status)));
  const mem = sys?.mem_bytes;
  const memUsed =
    mem?.MemTotal && mem.MemAvailable !== undefined ? 1 - mem.MemAvailable / mem.MemTotal : null;
  const temp = sys?.cpu_temp_c ?? null;
  const net = sys?.net ?? null;
  const vol = sys?.volume ?? null;
  const phone = sys?.phone ?? null;
  const wifiBars =
    net?.kind === 'wifi'
      ? net.signal == null
        ? 3
        : net.signal > 66
          ? 3
          : net.signal > 33
            ? 2
            : 1
      : 0;

  return (
    <div
      className="sbar-root"
      style={{ '--sbar-accent': color } as CSSProperties}
      data-menu={menu ?? undefined}
    >
      {menu && shown ? (
        <MenuLayer menu={menu} closing={closing} pinned={sys?.launchers ?? []} onClose={close} />
      ) : (
        <div className="smenu-spacer" onClick={menu ? close : undefined} />
      )}
      <div
        className="sbar"
        data-floating={floating}
        data-leaving={leaving}
        data-state={state}
        data-talking={line !== null}
        style={{ '--sbar-accent': color } as CSSProperties}
        role="status"
        aria-live="polite"
        aria-label={`Jarvis school mode, ${muted ? 'microphone muted' : STATE_LABEL[jarvis].toLowerCase()}`}
      >
        <span className="sbar-scan" aria-hidden="true" />
        <span key={state} className="sbar-sweep" aria-hidden="true" />

        {/* ---- Jarvis ---- */}
        <section className="sbar-left">
          <button
            type="button"
            className="sbar-start"
            data-open={menu === 'launcher'}
            aria-label="Applications"
            onClick={() => toggle('launcher')}
          >
            <Core />
          </button>
          <span className="sbar-id">
            <b className="sbar-id__name">JARVIS</b>
            <span key={state} className="sbar-id__state">
              {muted ? 'MUTED' : STATE_LABEL[jarvis]}
            </span>
          </span>
        </section>

        {/* ---- Apps ---- */}
        <Apps launchers={sys?.launchers ?? []} windows={sys?.windows ?? []} />

        {/* ---- Stream ---- */}
        <section className="sbar-mid">
          <span className="sbar-bars" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
            <i />
          </span>
          <span className="sbar-stream">
            {line ? (
              <StreamLine key={line.key} line={line} />
            ) : boot ? (
              <BootLine view={boot} />
            ) : (
              <LiveTrace state={state} />
            )}
          </span>
          {jobs.map((j) => (
            <span key={j.id} className="sbar-job" title={j.title}>
              <Ring pct={j.pct} />
              <span className="sbar-job__title">{j.title}</span>
              {j.pct !== null ? <b>{j.pct}%</b> : null}
            </span>
          ))}
        </section>

        {/* ---- Machine ---- */}
        <section className="sbar-right">
          <button
            type="button"
            className="sbar-tray"
            data-open={menu === 'quick'}
            aria-label="Quick settings"
            onClick={() => toggle('quick')}
          >
            {net ? (
              <Tile
                label={net.kind === 'none' ? 'OFFLINE' : net.kind === 'wifi' ? 'WI-FI' : 'LAN'}
                value={net.kind === 'none' ? '—' : net.name || 'connected'}
                icon={<WifiIcon bars={wifiBars} />}
                tone={net.kind === 'none' ? 'warn' : undefined}
              />
            ) : null}
            {vol ? (
              <Tile
                label="VOL"
                value={vol.muted ? 'MUTED' : `${vol.pct}%`}
                icon={<VolIcon muted={vol.muted} />}
                level={vol.muted ? 0 : vol.pct / 150}
                tone={vol.muted ? 'dim' : vol.pct > 100 ? 'warn' : undefined}
              />
            ) : null}
            {temp !== null ? (
              <Tile
                label="CPU"
                value={`${Math.round(temp)}°`}
                level={Math.min(1, temp / 100)}
                tone={temp >= 85 ? 'hot' : temp >= 72 ? 'warn' : undefined}
              />
            ) : null}
            {memUsed !== null ? (
              <Tile
                label="MEM"
                value={`${Math.round(memUsed * 100)}%`}
                level={memUsed}
                tone={memUsed > 0.9 ? 'warn' : undefined}
              />
            ) : null}
            {batt !== null ? (
              <Tile
                label={charging ? (batt >= 99 ? 'PLUGGED IN' : 'CHARGING') : 'BATTERY'}
                value={`${Math.round(batt)}%`}
                icon={<BattIcon level={batt / 100} charging={charging} />}
                tone={
                  batt <= 15 && !charging ? 'hot' : batt <= 30 && !charging ? 'warn' : undefined
                }
              />
            ) : null}
            {phone && phone.age_s < 900 ? (
              <Tile
                label="PHONE"
                value={`${Math.round(phone.battery)}%`}
                level={phone.battery / 100}
                tone={phone.battery <= 20 && !phone.charging ? 'warn' : undefined}
              />
            ) : null}
          </button>
          <button
            type="button"
            className="sbar-clock"
            data-open={menu === 'calendar'}
            aria-label="Calendar"
            onClick={() => toggle('calendar')}
          >
            <b>
              {pad(now.getHours())}
              <span className="sbar-clock__colon">:</span>
              {pad(now.getMinutes())}
            </b>
            <i>
              {now
                .toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
                .toUpperCase()}
            </i>
          </button>
        </section>
      </div>
    </div>
  );
}
