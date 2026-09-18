'use client';

import type { HudTask } from '@/hooks/hud/use-hud-events';

function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

const VERBS: Record<string, string> = {
  nmap_scan: 'scanning network…',
  nikto_sweep: 'sweeping web server…',
  gobuster_dir: 'enumerating paths…',
  open_app: 'opening app…',
  open_url: 'opening page…',
  take_screenshot: 'capturing screen…',
  read_screen_text: 'reading screen…',
  take_os_screenshot: 'capturing screen…',
};

/** Max 5 live tasks; completed collapse into the terminal log. */
export function ExecFeed({ live }: { live: HudTask[] }) {
  const items = live.slice(-5);

  return (
    <div className="hud-exec">
      <div className="hud-exec__head">
        <span>EXECUTION</span>
        <span className="hud-exec__count">{items.length}/5 live</span>
      </div>
      {items.length === 0 ? (
        <p className="hud-exec__empty">— no active tasks —</p>
      ) : (
        <ul className="hud-exec__list">
          {items.map((t) => (
            <li key={t.id} className="hud-exec__item">
              <span className="hud-exec__pulse" aria-hidden="true" />
              <span className="hud-exec__label">
                {t.label ?? VERBS[t.tool ?? ''] ?? `${t.tool ?? 'task'}…`}
              </span>
              <span className="hud-exec__time">{fmtElapsed(t.elapsedMs)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
