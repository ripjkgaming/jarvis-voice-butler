import type { ControlAction, Laptop, ActionState } from '../store';
import { Ticker } from '../ui/Captions';
import { DeckStatus } from '../ui/DeckStatus';
import { PowerTile } from '../ui/PowerTile';
import { Tile } from '../ui/Tile';

export function Deck(p: {
  lap: Laptop;
  host: string;
  now: number;
  can: boolean;
  states: Record<string, ActionState | undefined>;
  powerOpen: boolean;
  onBack: () => void;
  onAction: (a: ControlAction) => void;
  onPower: () => void;
  onResized: () => void;
}) {
  const { lap, now, states: s } = p;
  const g = lap.guest.value;
  const k = lap.locked.value;
  const sc = lap.screens.value;
  const mic = lap.micMuted.value;
  const lockTxt = k === null ? null : k ? 'LOCKED' : 'OPEN';
  const word = (v: string | null) => (v === null ? 'unknown' : v.toLowerCase());
  return (
    <section class="page" data-page="deck" aria-label="Deck">
      <header class="page-header">
        <h1>Laptop controls</h1>
        <button type="button" aria-label="Back to core" onClick={p.onBack} data-first="">Back</button>
      </header>
      <p class="muted">{p.host}</p>
      <Ticker />
      <DeckStatus />
      <div class="control-grid">
        <Tile
          id="GUEST"
          cls="c0 r0"
          label="GUEST"
          value={g === null ? null : g ? 'ON' : 'OFF'}
          at={lap.guest.at}
          now={now}
          state={s[g ? 'guest_off' : 'guest_on']}
          fingerprint={g === true}
          disabled={!p.can || g === null}
          pressed={g === true}
          aria={`Guest mode, ${g === null ? 'unknown' : g ? 'on' : 'off'}.${g ? ' Turning off needs fingerprint.' : ''}`}
          onTap={() => p.onAction(g ? 'guest_off' : 'guest_on')}
        />
        <Tile
          id="LOCK"
          cls="c1 r0"
          label="LOCK"
          value={lockTxt}
          at={lap.locked.at}
          now={now}
          state={s.lock}
          disabled={!p.can}
          aria={`Lock the laptop. Screen is ${word(lockTxt)}.`}
          onTap={() => p.onAction('lock')}
        />
        <Tile
          id="UNLOCK"
          cls="c0 r1"
          label="UNLOCK"
          value={lockTxt}
          at={lap.locked.at}
          now={now}
          state={s.unlock}
          fingerprint
          disabled={!p.can}
          aria={`Unlock the laptop. Screen is ${word(lockTxt)}. Needs fingerprint.`}
          onTap={() => p.onAction('unlock')}
        />
        <Tile
          id="SCREENS"
          cls="c1 r1"
          label="SCREENS"
          value={sc === null ? null : sc === 'on' ? 'ON' : 'OFF'}
          at={lap.screens.at}
          now={now}
          state={s[sc === 'off' ? 'screens_on' : 'screens_off']}
          disabled={!p.can}
          pressed={sc === 'on'}
          aria={`Screens, ${word(sc)}. Tap to turn ${sc === 'off' ? 'on' : 'off'}.`}
          onTap={() => p.onAction(sc === 'off' ? 'screens_on' : 'screens_off')}
        />
        <PowerTile
          power={lap.power}
          now={now}
          open={p.powerOpen}
          disabled={!p.can}
          states={s}
          onToggle={p.onPower}
          onAction={(a) => p.onAction(a)}
          onResized={p.onResized}
        />
        <Tile
          id="MIC"
          cls="wide mic"
          label="PC MIC"
          value={mic === null ? null : mic ? 'MUTED' : 'LIVE'}
          at={lap.micMuted.at}
          now={now}
          state={s[mic ? 'mic_unmute' : 'mic_mute']}
          disabled={!p.can}
          pressed={mic === true}
          aria={`Laptop wake microphone, ${mic === null ? 'unknown' : mic ? 'muted' : 'live'}. Tap to ${mic ? 'unmute' : 'mute'}.`}
          onTap={() => p.onAction(mic ? 'mic_unmute' : 'mic_mute')}
        />
      </div>
    </section>
  );
}
