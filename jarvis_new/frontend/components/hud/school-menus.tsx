'use client';

import { type CSSProperties, memo, useEffect, useMemo, useRef, useState } from 'react';
import { AmbientParticles } from '@/components/hud/ambient-particles';
import { ICON_MISS_TTL_MS, bridgeAppIcon, bridgeGet, bridgeLaunch, bridgePost } from '@/lib/bridge';

/** School taskbar menus. They open upward out of the bar: the shell grows
 *  the window by MENU_EXTRA (school.rs `school_menu`), the area around a
 *  menu stays transparent, and clicking it closes the menu. The window never
 *  takes keyboard focus, so everything here is pointer-driven (no search
 *  box: categories do that job). */

export type MenuKind = 'launcher' | 'quick' | 'calendar';

/** Height the window grows by while a menu is open (logical px). */
export const MENU_EXTRA = 480;

type App = { desktop: string; name: string; generic?: string; categories?: string[] };
type Quick = {
  wifi?: { on: boolean; ssid: string } | null;
  bluetooth?: { on: boolean } | null;
  volume?: { pct: number; muted: boolean } | null;
  brightness?: { pct: number } | null;
  dnd?: boolean | null;
};

/* ------------------------------ shared ------------------------------ */

export const AppIcon = memo(function AppIcon({ app, label }: { app: string; label: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    // Keep asking while it's a miss: the bridge may be restarting.
    const load = () =>
      void bridgeAppIcon(app).then((d) => {
        if (!live) return;
        setSrc(d);
        if (!d) retry = setTimeout(load, ICON_MISS_TTL_MS + 1000);
      });
    load();
    return () => {
      live = false;
      clearTimeout(retry);
    };
  }, [app]);
  return src ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt="" draggable={false} decoding="async" loading="lazy" />
  ) : (
    <span className="sbar-app__glyph">{(label || app || '?').slice(0, 1).toUpperCase()}</span>
  );
});

function MenuHead({ title, meta }: { title: string; meta?: string }) {
  return (
    <header className="smenu__head">
      <b>
        <span aria-hidden="true">◢</span> {title}
      </b>
      {meta ? <i>{meta}</i> : null}
    </header>
  );
}

/* ------------------------------ launcher ------------------------------ */

const CATEGORIES: { key: string; label: string; match: string[] }[] = [
  { key: 'all', label: 'All', match: [] },
  { key: 'edu', label: 'School', match: ['Education', 'Office', 'Science', 'Math'] },
  { key: 'net', label: 'Internet', match: ['Network', 'WebBrowser', 'Email', 'Chat'] },
  { key: 'dev', label: 'Dev', match: ['Development', 'IDE', 'TerminalEmulator'] },
  { key: 'media', label: 'Media', match: ['AudioVideo', 'Audio', 'Video', 'Graphics'] },
  { key: 'games', label: 'Games', match: ['Game'] },
  { key: 'sys', label: 'System', match: ['System', 'Settings', 'Utility'] },
];

const POWER: { action: string; label: string; confirm: boolean; icon: string }[] = [
  { action: 'lock', label: 'Lock', confirm: false, icon: 'M5 9V6a3 3 0 0 1 6 0v3M3.5 9h9v6h-9z' },
  {
    action: 'sleep',
    label: 'Sleep',
    confirm: false,
    icon: 'M11.5 10.5A5 5 0 0 1 5.5 4.5a5 5 0 1 0 6 6z',
  },
  {
    action: 'logout',
    label: 'Log out',
    confirm: true,
    icon: 'M9 3H3v10h6M7 8h7M11.5 5.5 14 8l-2.5 2.5',
  },
  {
    action: 'restart',
    label: 'Restart',
    confirm: true,
    icon: 'M13 8a5 5 0 1 1-1.5-3.6M13 2.5v2.5h-2.5',
  },
  { action: 'shutdown', label: 'Shut down', confirm: true, icon: 'M8 2v6M4.6 4.4a5 5 0 1 0 6.8 0' },
];

function PowerRow({ onDone }: { onDone: () => void }) {
  // Consequential actions arm on the first click and fire on the second.
  const [armed, setArmed] = useState<string | null>(null);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(null), 3500);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <footer className="smenu__power">
      {POWER.map((p) => (
        <button
          key={p.action}
          type="button"
          className="smenu__pbtn"
          data-armed={armed === p.action}
          data-danger={p.confirm}
          onClick={() => {
            if (p.confirm && armed !== p.action) {
              setArmed(p.action);
              return;
            }
            setArmed(null);
            onDone();
            void bridgePost('/power', { action: p.action });
          }}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d={p.icon} />
          </svg>
          <span>{armed === p.action ? 'Confirm?' : p.label}</span>
        </button>
      ))}
    </footer>
  );
}

let appsCache: App[] | null = null;

function LauncherMenu({
  pinned,
  onClose,
  ambientActive,
}: {
  pinned: { desktop: string; name: string }[];
  onClose: () => void;
  ambientActive: boolean;
}) {
  const [apps, setApps] = useState<App[] | null>(appsCache);
  const [cat, setCat] = useState('all');
  useEffect(() => {
    let live = true;
    void bridgeGet<{ ok?: boolean; apps?: App[] }>('/apps').then((j) => {
      if (live && j?.apps) {
        appsCache = j.apps;
        setApps(j.apps);
      }
    });
    return () => {
      live = false;
    };
  }, []);
  const shown = useMemo(() => {
    const c = CATEGORIES.find((x) => x.key === cat);
    if (!apps || !c || !c.match.length) return apps ?? [];
    return apps.filter((a) => a.categories?.some((k) => c.match.includes(k)));
  }, [apps, cat]);
  const launch = (desktop: string) => {
    onClose();
    void bridgeLaunch(desktop);
  };

  return (
    <div className="smenu smenu--launcher" role="menu" aria-label="Apps">
      <AmbientParticles variant="menu" active={ambientActive} className="smenu__ambient" />
      <MenuHead title="APPLICATIONS" meta={apps ? `${shown.length} APPS` : 'LOADING'} />
      {pinned.length ? (
        <div className="smenu__pinned" aria-label="Pinned applications">
          {pinned.map((p) => (
            <button
              key={p.desktop}
              type="button"
              className="smenu__pin"
              title={p.name}
              aria-label={p.name}
              onClick={() => launch(p.desktop)}
            >
              <AppIcon app={p.desktop} label={p.name} />
            </button>
          ))}
        </div>
      ) : null}
      <div className="smenu__body">
        <nav className="smenu__cats" aria-label="Application categories">
          {CATEGORIES.map((c) => (
            <button
              key={c.key}
              type="button"
              data-on={cat === c.key}
              aria-pressed={cat === c.key}
              onClick={() => setCat(c.key)}
            >
              {c.label}
            </button>
          ))}
        </nav>
        <div className="smenu__grid">
          {shown.map((a, i) => (
            <button
              key={a.desktop}
              type="button"
              className="smenu__app"
              title={a.generic || a.name}
              style={{ '--i': Math.min(i, 24) } as CSSProperties}
              onClick={() => launch(a.desktop)}
            >
              <AppIcon app={a.desktop} label={a.name} />
              <span>{a.name}</span>
            </button>
          ))}
          {apps && !shown.length ? <p className="smenu__empty">Nothing in this group.</p> : null}
        </div>
      </div>
      <PowerRow onDone={onClose} />
    </div>
  );
}

/* ------------------------------ quick settings ------------------------------ */

function Toggle({
  label,
  detail,
  on,
  icon,
  onClick,
}: {
  label: string;
  detail: string;
  on: boolean | null;
  icon: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="smenu__toggle"
      data-on={on === true}
      aria-pressed={on === true}
      disabled={on === null}
      onClick={onClick}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d={icon} />
      </svg>
      <span>
        <b>{label}</b>
        <i>{detail}</i>
      </span>
    </button>
  );
}

function Slider({
  label,
  value,
  onChange,
  muted,
  onMute,
  max = 100,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  muted?: boolean;
  onMute?: () => void;
  /** Above 100 = boost range (PipeWire goes to 150%); marked at 100. */
  max?: number;
}) {
  return (
    <label
      className="smenu__slider"
      data-muted={!!muted}
      data-boost={value > 100}
      data-boostable={max > 100}
      style={{ '--mark': `${(100 / max) * 100}%`, '--mark-f': 100 / max } as CSSProperties}
    >
      <span className="smenu__slider-head">
        <b>{label}</b>
        <i>{muted ? 'MUTED' : `${value}%`}</i>
        {onMute ? (
          <button type="button" onClick={onMute}>
            {muted ? 'UNMUTE' : 'MUTE'}
          </button>
        ) : null}
      </span>
      <input
        type="range"
        aria-label={label}
        min={1}
        max={max}
        value={value}
        style={{ '--v': `${(value / max) * 100}%` } as CSSProperties}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

function QuickMenu({ ambientActive }: { ambientActive: boolean }) {
  const [q, setQ] = useState<Quick | null>(null);
  // Local slider values win over polling while Sir is dragging.
  const [vol, setVol] = useState<number | null>(null);
  const [bri, setBri] = useState<number | null>(null);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const pending = useRef<Record<string, Record<string, unknown>>>({});
  const lastEdit = useRef<Record<string, number>>({});

  useEffect(() => {
    let live = true;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      clearTimeout(timer);
      if (!live || document.hidden || inFlight) return;
      inFlight = true;
      const j = await bridgeGet<Quick & { ok?: boolean }>('/quick');
      inFlight = false;
      if (!live) return;
      if (j) {
        setQ(j);
        // Release the drag override once a later poll can confirm the value.
        if (Date.now() - (lastEdit.current.volume ?? 0) > 1500) setVol(null);
        if (Date.now() - (lastEdit.current.brightness ?? 0) > 1500) setBri(null);
      }
      if (!document.hidden) timer = setTimeout(poll, 3000);
    };
    void poll();
    document.addEventListener('visibilitychange', poll);
    return () => {
      live = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', poll);
      // Preserve the final slider value even if the menu closes before debounce.
      Object.values(timers.current).forEach(clearTimeout);
      Object.values(pending.current).forEach((body) => void bridgePost('/quick', body));
      timers.current = {};
      pending.current = {};
    };
  }, []);

  const send = (body: Record<string, unknown>, then?: (q: Quick) => Quick) => {
    if (then) setQ((current) => (current ? then(current) : current));
    void bridgePost('/quick', body);
  };
  const debounced = (key: string, body: Record<string, unknown>) => {
    lastEdit.current[key] = Date.now();
    pending.current[key] = body;
    clearTimeout(timers.current[key]);
    timers.current[key] = setTimeout(() => {
      delete pending.current[key];
      delete timers.current[key];
      void bridgePost('/quick', body);
    }, 100);
  };

  const wifi = q?.wifi ?? null;
  const bt = q?.bluetooth ?? null;
  const volume = q?.volume ?? null;
  const bright = q?.brightness ?? null;

  return (
    <div className="smenu smenu--quick" role="menu" aria-label="Quick settings">
      <AmbientParticles variant="menu" active={ambientActive} className="smenu__ambient" />
      <MenuHead title="QUICK SETTINGS" meta={q ? 'LIVE' : 'LOADING'} />
      <div className="smenu__toggles">
        <Toggle
          label="Wi-Fi"
          detail={wifi ? (wifi.on ? wifi.ssid || 'On' : 'Off') : '—'}
          on={wifi ? wifi.on : null}
          icon="M2 6.5a9 9 0 0 1 12 0M4.2 8.8a6 6 0 0 1 7.6 0M6.4 11a3 3 0 0 1 3.2 0M8 13.2h.01"
          onClick={() =>
            wifi &&
            send({ action: 'wifi', on: !wifi.on }, (s) => ({
              ...s,
              wifi: { ...wifi, on: !wifi.on },
            }))
          }
        />
        <Toggle
          label="Bluetooth"
          detail={bt ? (bt.on ? 'On' : 'Off') : '—'}
          on={bt ? bt.on : null}
          icon="M5 5l6 6-3 3V2l3 3-6 6"
          onClick={() =>
            bt &&
            send({ action: 'bluetooth', on: !bt.on }, (s) => ({ ...s, bluetooth: { on: !bt.on } }))
          }
        />
        <Toggle
          label="Focus"
          detail={q?.dnd == null ? '—' : q.dnd ? 'Do not disturb' : 'Notifications on'}
          on={q?.dnd ?? null}
          icon="M12.5 10.5A5 5 0 0 1 5.5 3.5a5 5 0 1 0 7 7z"
          onClick={() =>
            q?.dnd != null && send({ action: 'dnd', on: !q.dnd }, (s) => ({ ...s, dnd: !q.dnd }))
          }
        />
      </div>
      {volume ? (
        <Slider
          label="VOLUME"
          max={150}
          value={vol ?? volume.pct}
          muted={volume.muted}
          onMute={() =>
            send({ action: 'mute', on: !volume.muted }, (s) => ({
              ...s,
              volume: { ...volume, muted: !volume.muted },
            }))
          }
          onChange={(v) => {
            setVol(v);
            debounced('volume', { action: 'volume', pct: v });
          }}
        />
      ) : null}
      {bright ? (
        <Slider
          label="BRIGHTNESS"
          value={bri ?? bright.pct}
          onChange={(v) => {
            setBri(v);
            debounced('brightness', { action: 'brightness', pct: v });
          }}
        />
      ) : null}
    </div>
  );
}

/* ------------------------------ calendar ------------------------------ */

function CalendarMenu({ ambientActive }: { ambientActive: boolean }) {
  const today = new Date();
  const [offset, setOffset] = useState(0);
  const first = new Date(today.getFullYear(), today.getMonth() + offset, 1);
  const lead = (first.getDay() + 6) % 7; // Monday first
  const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)];
  const isToday = (d: number) => offset === 0 && d === today.getDate();
  return (
    <div className="smenu smenu--calendar" role="menu" aria-label="Calendar">
      <AmbientParticles variant="menu" active={ambientActive} className="smenu__ambient" />
      <header className="smenu__head">
        <b>
          {first.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }).toUpperCase()}
        </b>
        <span className="smenu__nav">
          <button type="button" onClick={() => setOffset((o) => o - 1)} aria-label="Previous month">
            ‹
          </button>
          <button type="button" onClick={() => setOffset(0)} aria-label="This month">
            •
          </button>
          <button type="button" onClick={() => setOffset((o) => o + 1)} aria-label="Next month">
            ›
          </button>
        </span>
      </header>
      <div className="smenu__big">
        <b>{today.toLocaleDateString(undefined, { weekday: 'long' })}</b>
        <i>
          {today.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}
        </i>
      </div>
      <div className="smenu__cal">
        {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => (
          <i key={`h${i}`}>{d}</i>
        ))}
        {cells.map((d, i) =>
          d === null ? (
            <span key={`e${i}`} />
          ) : (
            <span key={d} data-today={isToday(d)} data-weekend={(lead + d - 1) % 7 >= 5}>
              {d}
            </span>
          )
        )}
      </div>
    </div>
  );
}

/* ------------------------------ layer ------------------------------ */

/** The menu layer above the bar. Clicking anywhere outside a menu closes
 *  it; so does the pointer leaving the window for a moment after it was in
 *  it (the stand-in for focus loss, which an unfocusable window never
 *  gets). */
export function MenuLayer({
  menu,
  closing,
  pinned,
  onClose,
  active = true,
}: {
  menu: MenuKind;
  closing: boolean;
  pinned: { desktop: string; name: string }[];
  onClose: () => void;
  active?: boolean;
}) {
  useEffect(() => {
    // Only once the pointer has been in the window: a menu opened by voice
    // (jarvis-shell schoolmenu) must not close just because nobody moved.
    let t: ReturnType<typeof setTimeout> | undefined;
    let visited = false;
    const leave = () => {
      clearTimeout(t);
      if (visited) t = setTimeout(onClose, 1400);
    };
    const enter = () => {
      visited = true;
      clearTimeout(t);
    };
    const root = document.documentElement;
    root.addEventListener('mouseleave', leave);
    root.addEventListener('mouseenter', enter);
    root.addEventListener('mousemove', enter, { once: true });
    return () => {
      clearTimeout(t);
      root.removeEventListener('mouseleave', leave);
      root.removeEventListener('mouseenter', enter);
      root.removeEventListener('mousemove', enter);
    };
  }, [onClose]);

  return (
    <div
      className="smenu-layer"
      data-menu={menu}
      data-closing={closing}
      aria-hidden={closing}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {menu === 'launcher' ? (
        <LauncherMenu pinned={pinned} onClose={onClose} ambientActive={active && !closing} />
      ) : null}
      {menu === 'quick' ? <QuickMenu ambientActive={active && !closing} /> : null}
      {menu === 'calendar' ? <CalendarMenu ambientActive={active && !closing} /> : null}
    </div>
  );
}
