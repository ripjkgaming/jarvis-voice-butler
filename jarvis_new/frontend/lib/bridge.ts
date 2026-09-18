'use client';

/** Bridge client for the desktop shell's localhost control plane
 *  (src/bridge.py, 127.0.0.1:4317). Falls back to the legacy /api/*
 *  dev routes when the bridge is unreachable and we're not in Tauri. */

const BRIDGE_PORT = Number(process.env.NEXT_PUBLIC_BRIDGE_PORT ?? 4317);
const BRIDGE_URL = `http://127.0.0.1:${BRIDGE_PORT}`;

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BRIDGE_URL}${path}`, { cache: 'no-store' });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export type BridgeSys = {
  ok?: boolean;
  load_1_5_15?: string[];
  mem_bytes?: { MemTotal?: number; MemAvailable?: number };
  home_free_bytes?: number;
};

export type BridgeActions = {
  ok?: boolean;
  actions?: string[];
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
