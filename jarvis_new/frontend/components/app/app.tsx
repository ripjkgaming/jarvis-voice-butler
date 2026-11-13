'use client';

import { useEffect, useState } from 'react';
import { WarningIcon } from '@phosphor-icons/react/dist/ssr';
import type { AppConfig } from '@/app-config';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { ViewController } from '@/components/app/view-controller';
import { CallHudLive } from '@/components/calls/call-hud-live';
import { HudShell } from '@/components/hud/hud-shell';
import { Toaster } from '@/components/ui/sonner';
import { useRoom } from '@/hooks/hud/use-room-state';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { bridgeObserverToken } from '@/lib/bridge';
import { setHudRoom } from '@/lib/hud-room';
import { isTauri, mintToken, shellAppConfig } from '@/lib/tauri';
import { VoiceSession } from '@/lib/voice-session';

function AppSetup() {
  useAgentErrors();

  return null;
}

interface AppProps {
  appConfig: AppConfig;
}

/** Receive-only token for a live call. The Tauri shell mints through its
 *  command; a plain browser asks the bridge. Neither dispatches an agent. */
function observerToken(room: string) {
  return isTauri() ? mintToken(room) : bridgeObserverToken(room);
}

export function App({ appConfig }: AppProps) {
  const [config, setConfig] = useState(appConfig);
  const [voice] = useState(() => new VoiceSession());
  const liveRoom = useRoom();

  // In the Tauri shell, agentName lives in the shell config (env AGENT_NAME),
  // not in a build-time env var. Pull it once on mount.
  useEffect(() => {
    if (!isTauri()) return;
    shellAppConfig()
      .then((c) => {
        if (c?.agentName && c.agentName !== config.agentName) {
          setConfig((prev) => ({ ...prev, agentName: c.agentName }));
        }
      })
      .catch(() => {
        /* keep defaults */
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Receive-only link: follow the live call. No room, no socket. The room
  // watcher publishes the target for other readers (setHudRoom).
  useEffect(() => {
    setHudRoom(liveRoom);
    if (!liveRoom) return;

    let active = true;
    const fetchToken = () => observerToken(liveRoom).then((d) => d.participantToken);
    observerToken(liveRoom).then(
      (details) => {
        if (active) voice.connect(details.serverUrl, details.participantToken, { fetchToken });
      },
      () => {
        /* no link to the call yet; the next room change retries */
      }
    );
    return () => {
      active = false;
      voice.disconnect();
    };
  }, [liveRoom, voice]);

  return (
    // Muted renderer: the native wake client owns the laptop speakers, so
    // the webview must never double-play the agent. Eyes here, ears there.
    <AgentSessionProvider session={voice} muted>
      <AppSetup />
      <CallHudLive />
      <HudShell>
        <main className="contents">
          <ViewController />
        </main>
      </HudShell>
      <Toaster
        icons={{
          warning: <WarningIcon weight="bold" />,
        }}
        position="top-center"
        className="toaster group"
        style={
          {
            '--normal-bg': 'var(--popover)',
            '--normal-text': 'var(--popover-foreground)',
            '--normal-border': 'var(--border)',
          } as React.CSSProperties
        }
      />
    </AgentSessionProvider>
  );
}
