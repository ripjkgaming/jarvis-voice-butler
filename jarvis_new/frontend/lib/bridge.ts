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
