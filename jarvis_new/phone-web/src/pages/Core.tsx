import { call } from '../store';
import { Captions } from '../ui/Captions';
import { CommandLine } from '../ui/CommandLine';
import type { Geo } from '../ui/Reactor';

export function Core(p: {
  geo: Geo;
  live: boolean;
  muted: boolean;
  online: boolean;
  onReactor: () => void;
  onMute: () => void;
  onNav: () => void;
  onCommand: (t: string) => Promise<void>;
}) {
  const phase = call.value.phase;
  const status: Record<string, string> = {
    idle: 'Ready to call',
    minting: 'Preparing call',
    connecting: 'Connecting',
    ending: 'Ending call',
    live: 'Call connected',
    reconnecting: 'Reconnecting',
    error: 'Call unavailable',
  };
  return (
    <section class="page" data-page="core" aria-label="Voice and messages">
      <header class="page-header">
        <h1>Locke Link</h1>
        <button type="button" aria-label="Open deck" onClick={p.onNav}>Laptop controls</button>
      </header>
      <section class="panel" aria-labelledby="call-title">
        <h2 id="call-title">Voice call</h2>
        <p role="status">{status[phase] ?? phase}</p>
        <div class="actions">
          <button type="button" data-first="" aria-label={p.live ? 'End call' : 'Start call'} disabled={!p.online && !p.live} onClick={p.onReactor}>
            {p.live ? 'End call' : 'Call Jarvis'}
          </button>
          <button type="button" disabled={phase !== 'live'} aria-pressed={p.muted} onClick={p.onMute}>
            {p.muted ? 'Unmute phone microphone' : 'Mute phone microphone'}
          </button>
        </div>
        {!p.online && <p class="muted">Connect to your laptop to start a call or send a message.</p>}
      </section>
      <section class="panel" aria-labelledby="messages-title">
        <h2 id="messages-title">Messages</h2>
        <Captions />
        <CommandLine disabled={!p.online} onSend={p.onCommand} />
      </section>
    </section>
  );
}
