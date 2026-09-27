'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AudioLines, Mic, MicOff, Radio, Sparkles } from 'lucide-react';
import { useMicMuted } from '@/hooks/hud/use-jarvis-state';
import { bridgeSummon } from '@/lib/bridge';
import { sayText } from '@/lib/hud-say';
import { isTauri, summonTalk } from '@/lib/tauri';

/** Voice-only interaction surface; it summons the native voice path, never text chat. */
export function VoiceDock({ active }: { active: boolean }) {
  const { muted, toggle: toggleMute } = useMicMuted();
  const [summoning, setSummoning] = useState(false);
  const summoningRef = useRef(false);
  const live = active;

  const summon = useCallback(async () => {
    if (summoningRef.current || live || muted === true) return;
    summoningRef.current = true;
    setSummoning(true);
    try {
      const ok = isTauri() ? (await summonTalk()).ok === true : await bridgeSummon();
      if (!ok) sayText('(voice summon unreachable — bridge offline?)');
    } catch {
      sayText('(voice summon unreachable — bridge offline?)');
    } finally {
      summoningRef.current = false;
      setSummoning(false);
    }
  }, [live, muted]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.code !== 'NumpadEnter' ||
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey
      ) {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable ||
        ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName ?? '')
      ) {
        return;
      }
      event.preventDefault();
      void summon();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [summon]);

  const mutedLabel =
    muted === true
      ? 'Microphone muted'
      : muted === false
        ? 'Microphone live'
        : 'Checking microphone';
  const voiceLabel = live
    ? 'Voice link active'
    : summoning
      ? 'Connecting to JARVIS'
      : 'Talk to JARVIS';

  return (
    <div
      className="hud-voice"
      data-live={live ? 'true' : 'false'}
      data-summoning={summoning ? 'true' : 'false'}
      data-muted={muted === true ? 'true' : 'false'}
    >
      <button
        type="button"
        className="hud-voice__summon"
        onClick={() => void summon()}
        disabled={summoning || live || muted === true}
        aria-label={
          muted === true ? 'Talk to JARVIS unavailable while microphone is muted' : voiceLabel
        }
        aria-keyshortcuts="NumpadEnter"
      >
        <span className="hud-voice__summon-icon" aria-hidden="true">
          {live ? <AudioLines /> : summoning ? <Sparkles /> : <Radio />}
        </span>
        <span className="hud-voice__summon-copy">
          <strong>{voiceLabel}</strong>
        </span>
      </button>
      <button
        type="button"
        className="hud-voice__mute"
        onClick={() => void toggleMute()}
        aria-pressed={muted === true}
        aria-label={muted === true ? 'Unmute microphone' : 'Mute microphone'}
        title={muted === true ? 'Unmute microphone (M)' : 'Mute microphone (M)'}
      >
        <span className="hud-voice__mute-icon" aria-hidden="true">
          {muted === true ? <MicOff /> : <Mic />}
        </span>
        <span>{mutedLabel}</span>
        <span className="hud-voice__mute-state" aria-hidden="true">
          {muted === true ? 'OFF' : muted === false ? 'ON' : '—'}
        </span>
      </button>
    </div>
  );
}
