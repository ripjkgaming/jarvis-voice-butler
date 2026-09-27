'use client';

import { useEffect, useMemo, useState } from 'react';
import { TokenSource } from 'livekit-client';
import { useSession } from '@livekit/components-react';
import { WarningIcon } from '@phosphor-icons/react/dist/ssr';
import type { AppConfig } from '@/app-config';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { JarvisBackground } from '@/components/app/jarvis-background';
import { ViewController } from '@/components/app/view-controller';
import { HudShell } from '@/components/hud/hud-shell';
import { Toaster } from '@/components/ui/sonner';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { useDebugMode } from '@/hooks/useDebug';
import { getHudRoom } from '@/lib/hud-room';
import { isTauri, mintToken, shellAppConfig } from '@/lib/tauri';
import { getSandboxTokenSource } from '@/lib/utils';

const IN_DEVELOPMENT = process.env.NODE_ENV !== 'production';

function AppSetup() {
  useDebugMode({ enabled: IN_DEVELOPMENT });
  useAgentErrors();

  return null;
}

interface AppProps {
  appConfig: AppConfig;
}

export function App({ appConfig }: AppProps) {
  const [config, setConfig] = useState(appConfig);

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

  const tokenSource = useMemo(() => {
    if (typeof process.env.NEXT_PUBLIC_CONN_DETAILS_ENDPOINT === 'string') {
      return getSandboxTokenSource(config);
    }
    if (isTauri()) {
      // Receive-only: join the live wake room (set by the room watcher).
      // No room → reject (minting blind would summon a stray second call).
      return TokenSource.custom(async () => mintToken(getHudRoom() ?? undefined));
    }
    // Plain-browser dev fallback (no shell): legacy endpoint route.
    return TokenSource.endpoint('/api/token');
  }, [config]);

  const session = useSession(
    tokenSource,
    config.agentName ? { agentName: config.agentName } : undefined
  );

  if (IN_DEVELOPMENT && !config.agentName) {
    console.warn(
      '[jarvis] agentName is undefined - no explicit dispatch will be sent and the named worker will NOT join. Set AGENT_NAME in the shell env or frontend/.env.local.'
    );
  }

  return (
    // Muted renderer: the native wake client owns the laptop speakers, so
    // the webview must never double-play the agent. Eyes here, ears there.
    <AgentSessionProvider session={session} muted>
      <AppSetup />
      <JarvisBackground />
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
