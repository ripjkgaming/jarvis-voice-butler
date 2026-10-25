'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Reply, Snapshot } from './types';

const DEFAULT_PORT = 4320;

function wsUrl(): string {
  let port = DEFAULT_PORT;
  if (typeof window !== 'undefined') {
    const q = new URLSearchParams(window.location.search).get('wsport');
    if (q && Number(q) > 0) port = Number(q);
  }
  return `ws://127.0.0.1:${port}`;
}

export interface GamesConnection {
  snap: Snapshot | null;
  connected: boolean;
  send: (name: string, args?: Record<string, unknown>) => Promise<Reply | null>;
}

/** Live link to the games server; reconnects until the game ends. */
export function useGames(): GamesConnection {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const pending = useRef(new Map<number, (r: Reply | null) => void>());
  const nextId = useRef(1);

  useEffect(() => {
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(wsUrl());
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === 'snapshot') setSnap(msg.data as Snapshot);
          else if (msg.type === 'reply') {
            const cb = pending.current.get(msg.id);
            if (cb) {
              pending.current.delete(msg.id);
              cb(msg as Reply);
            }
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        for (const cb of pending.current.values()) cb(null);
        pending.current.clear();
        if (!closed) timer = setTimeout(connect, 1000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      wsRef.current?.close();
    };
  }, []);

  const send = useCallback((name: string, args: Record<string, unknown> = {}) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.resolve(null);
    const id = nextId.current++;
    return new Promise<Reply | null>((resolve) => {
      pending.current.set(id, resolve);
      ws.send(JSON.stringify({ type: 'command', id, name, args }));
      setTimeout(() => {
        if (pending.current.delete(id)) resolve(null);
      }, 20000);
    });
  }, []);

  return { snap, connected, send };
}
