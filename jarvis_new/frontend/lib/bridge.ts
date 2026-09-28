'use client';

/** Bridge client for the shell's control plane (src/bridge.py).
 *
 *  Inside Tauri the base URL + token come from `invoke('bridge_info')` —
 *  the bridge may bind the tailnet IP (phone access), never assume
 *  loopback. Outside Tauri (plain-browser dev) it falls back to
 *  unauthenticated 127.0.0.1:4317. Every call is fail-soft (null/[]).
 */
import { bridgeInfo, isTauri } from '@/lib/tauri';

const FALLBACK_URL = `http://127.0.0.1:${process.env.NEXT_PUBLIC_BRIDGE_PORT ?? 4317}`;

type BridgeEndpoint = { url: string; token?: string | null };
let endpoint: Promise<BridgeEndpoint> | null = null;

function getEndpoint(): Promise<BridgeEndpoint> {
  if (!endpoint) {
    endpoint = isTauri()
      ? bridgeInfo().catch(() => ({ url: FALLBACK_URL }))
      : Promise.resolve({ url: FALLBACK_URL });
  }
  return endpoint;
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const { url, token } = await getEndpoint();
    const res = await fetch(`${url}${path}`, {
      cache: 'no-store',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export type BridgeSys = {
  ok?: boolean;
  load_1_5_15?: string[];
  cpu_count?: number;
  mem_bytes?: { MemTotal?: number; MemAvailable?: number };
  home_free_bytes?: number;
  phone?: { battery: number; charging: boolean; age_s: number } | null;
  /** Phone presence per Tailscale; null = tailscale unavailable. */
  phone_tailnet?: { online: boolean; name: string } | null;
  /** Running research jobs with live progress (0-100) and current step. */
  research?: { id: string; title: string; progress: number; stage: string }[];
  /** "school" = Jarvis's click-through taskbar replaces the HUD. */
  mode?: 'school' | 'normal';
  /** Epoch seconds the current mode began (school-mode session timer). */
  mode_since?: number | null;
  net?: { kind: 'wifi' | 'ethernet' | 'none'; name: string; signal: number | null } | null;
  volume?: { pct: number; muted: boolean } | null;
  /** Open taskbar windows, in first-seen order (school-mode taskbar). */
  windows?: BridgeWindow[];
  /** Pinned taskbar launchers, in Plasma's order (icontasks config). */
  launchers?: { desktop: string; name: string }[];
  laptop_power?: {
    battery: number | null;
    status: string;
    watts: number | null;
    ac?: boolean;
  } | null;
  cpu_temp_c?: number | null;
  call_live?: boolean;
};

export type BridgeActions = {
  ok?: boolean;
  actions?: string[];
};

export type BridgeRoom = {
  ok?: boolean;
  room?: string | null;
};

/** Tail of ~/.jarvis/actions.log via bridge /actions (default 50 lines). */
export async function bridgeActions(limit = 50): Promise<string[]> {
  const j = await getJson<BridgeActions>(`/actions?limit=${limit}`);
  return j?.actions ?? [];
}

/** System gauges via bridge /sys (load avg + mem + home disk free). */
export async function bridgeSys(): Promise<BridgeSys | null> {
  return getJson<BridgeSys>('/sys');
}

/** Live wake room (wake publishes on summon, clears on hangup). Null =
 *  no call right now (or bridge unreachable — same standby treatment). */
export async function bridgeRoom(): Promise<string | null> {
  const j = await getJson<BridgeRoom>('/room');
  const room = j?.room;
  return typeof room === 'string' && room.length > 0 ? room : null;
}

type Rect = { x: number; y: number; w: number; h: number };

/** Geometry for the school entry transition, measured by the shell's KWin
 *  script (school.rs measure_script). `hud` is relative to `out`; `dir` is
 *  where the primary screen lies from `out` (null when they're the same). */
export type SchoolGeom = {
  nonce: string;
  hud: Rect;
  out: Rect;
  primary: Rect;
  same: boolean;
  dir: 'left' | 'right' | 'up' | 'down' | null;
  panel: number;
};

/** The geometry report for `nonce`, or null when it hasn't landed yet. */
export async function bridgeSchoolGeom(nonce: string): Promise<SchoolGeom | null> {
  const j = await getJson<{ geom?: SchoolGeom | null }>('/school/geom');
  return j?.geom && j.geom.nonce === nonce ? j.geom : null;
}

/** Call presence for the HUD colour: a live room, or the wake word just
 *  fired and the room is still coming up (`waking`). null = bridge down. */
export async function bridgeCallState(): Promise<{ live: boolean } | null> {
  const j = await getJson<BridgeRoom & { waking?: boolean }>('/room');
  if (!j) return null;
  return { live: (typeof j.room === 'string' && j.room.length > 0) || j.waking === true };
}

export type BridgeCaptions = {
  ok?: boolean;
  captions?: { ts: number; role: string; text: string }[];
};

/** Conversation tail (agent mirrors convo here; the webview has no
 *  LiveKit client, so this is the transcript). Fail-soft []. */
export async function bridgeCaptions(limit = 10): Promise<BridgeCaptions['captions']> {
  const j = await getJson<BridgeCaptions>(`/captions?limit=${limit}`);
  return Array.isArray(j?.captions) ? j.captions : [];
}

export type BridgeLiveCaption = { id: string; text: string; ts: number; done: boolean };

/** Jarvis's in-progress line, grown word by word in step with his audio
 *  (the agent mirrors its synced transcription). Null when none/stale. */
export async function bridgeLiveCaption(): Promise<BridgeLiveCaption | null> {
  const j = await getJson<{ ok?: boolean; live?: BridgeLiveCaption | null }>('/caption/live');
  const live = j?.live;
  return live && typeof live.text === 'string' ? live : null;
}

export type ActivityKind = 'download' | 'update' | 'coding' | 'research' | 'build' | 'task';

/** One system activity (downloads, package updates, coding/research
 *  projects, Jarvis tools) from src/activity.py. `progress` null means
 *  indeterminate; times are epoch seconds. */
export type BridgeActivity = {
  id: string;
  kind: ActivityKind | string;
  title: string;
  detail?: string;
  progress: number | null;
  status: 'running' | 'done' | 'failed' | 'cancelled' | string;
  started: number;
  updated: number;
  finished?: number | null;
  source?: string;
  meta?: { bytes_done?: number; bytes_total?: number; speed_bps?: number; eta_s?: number };
};

/** Running activities first, then the recently finished. Null when the
 *  bridge is unreachable (keep the last good list), [] when quiet. */
export async function bridgeActivity(): Promise<BridgeActivity[] | null> {
  const j = await getJson<{ ok?: boolean; items?: BridgeActivity[] }>('/activity');
  return j && Array.isArray(j.items) ? j.items : null;
}

export type BridgeChatReply = {
  ok?: boolean;
  reply?: string;
  warning?: string;
};

/** Typed side-channel: text to Jarvis (same brain as the phone Chat tab).
 *  Returns the reply text, or null when unreachable. */
export async function bridgeChat(text: string): Promise<string | null> {
  try {
    const { url, token } = await getEndpoint();
    const res = await fetch(`${url}/chat`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ text: text.slice(0, 2000) }),
    });
    if (!res.ok) return null;
    const j = (await res.json()) as BridgeChatReply;
    if (j.reply) return j.reply;
    return j.warning ? `(${j.warning})` : null;
  } catch {
    return null;
  }
}

/** PTT summon without the shell (plain-browser dev): POST /summon asks
 *  the wake listener for a talk session. True when the listener acked. */
export async function bridgeSummon(): Promise<boolean> {
  try {
    const { url, token } = await getEndpoint();
    const res = await fetch(`${url}/summon`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({}),
    });
    if (!res.ok) return false;
    const j = (await res.json()) as { ok?: boolean };
    return j.ok === true;
  } catch {
    return false;
  }
}

/** Generic authenticated GET for feature clients (lib/projects.ts). */
export function bridgeGet<T>(path: string): Promise<T | null> {
  return getJson<T>(path);
}

/** Generic authenticated POST (JSON in, JSON out). Null on any failure;
 *  non-2xx bodies are still returned so callers can show the error. */
export async function bridgePost<T>(path: string, body: unknown): Promise<T | null> {
  try {
    const { url, token } = await getEndpoint();
    const res = await fetch(`${url}${path}`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body ?? {}),
    });
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** One open app window (KWin push, src/active_window.py). */
export type BridgeWindow = {
  id: string;
  title: string;
  app: string;
  desktop: string;
  active: boolean;
  minimized: boolean;
  pid: number;
};

const iconCache = new Map<string, Promise<string | null>>();
/** A miss (bridge down, icon not installed yet) is forgotten after this. */
export const ICON_MISS_TTL_MS = 60_000;

/** App icon as a data URI (theme lookup on the bridge), cached per app.
 *  Hits are kept for the page's life; misses expire so they get retried. */
export function bridgeAppIcon(app: string): Promise<string | null> {
  const key = app.toLowerCase();
  let hit = iconCache.get(key);
  if (!hit) {
    hit = getJson<{ ok?: boolean; data?: string }>(`/appicon?app=${encodeURIComponent(app)}`).then(
      (j) => {
        const data = j?.ok && j.data ? j.data : null;
        if (!data) setTimeout(() => iconCache.delete(key), ICON_MISS_TTL_MS);
        return data;
      }
    );
    iconCache.set(key, hit);
  }
  return hit;
}

/** Activate or minimize one window by its KWin id. */
export async function bridgeWindowAction(
  id: string,
  action: 'activate' | 'minimize'
): Promise<boolean> {
  const j = await bridgePost<{ ok?: boolean }>('/window', { id, action });
  return !!j?.ok;
}

/** Launch an app by its desktop-file id (pinned taskbar launcher). */
export async function bridgeLaunch(desktop: string): Promise<boolean> {
  const j = await bridgePost<{ ok?: boolean }>('/launch', { desktop });
  return !!j?.ok;
}
