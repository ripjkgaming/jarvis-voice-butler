'use client';

/** Minimal Tauri v2 IPC bridge. Kept dependency-free: the shell injects
 *  `window.__TAURI_INTERNALS__` into the webview (devUrl AND tauri://localhost),
 *  so we never import @tauri-apps/api. Feature-detect instead.
 */

export type ConnectionDetails = {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
};

type TauriInternals = {
  invoke: (cmd: string, args?: unknown) => Promise<unknown>;
};

function internals(): TauriInternals | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { __TAURI_INTERNALS__?: TauriInternals };
  return w.__TAURI_INTERNALS__ ?? null;
}

/** True when running inside the Tauri webview (dev or packaged). */
export function isTauri(): boolean {
  return internals() !== null;
}

/** Call a Rust `#[tauri::command]`. Throws when outside Tauri. */
export async function invoke<T>(cmd: string, args?: unknown): Promise<T> {
  const api = internals();
  if (!api) throw new Error('not running inside the Tauri shell');
  return (await api.invoke(cmd, args)) as T;
}

/** Mint a participant token + connection details via `invoke('mint_token')`.
 *  Mirrors the old /api/token response so livekit's TokenSource.custom works. */
export function mintToken(roomConfig?: unknown): Promise<ConnectionDetails> {
  return invoke<ConnectionDetails>('mint_token', { roomConfig });
}

/** Shell config surface (agentName, etc.) via `invoke('app_config')`. */
export function shellAppConfig(): Promise<{ agentName?: string }> {
  return invoke<{ agentName?: string }>('app_config');
}
