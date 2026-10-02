'use client';

import { useEffect, useRef } from 'react';
import type { VoiceLink } from '@/lib/voice-link';
import './voice-link.css';

const STAGES = ['REQUEST', 'VOICE RELAY', 'AGENT LINK'];

/** Pause both normal and compact ornaments when detached from view. Native
 * shell hiding also sets hud-hidden; document visibility covers browser tabs. */
function useLinkVisibility() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    let intersects = true;
    const sync = () => {
      node.dataset.visible = String(intersects && !document.hidden);
    };
    const observer = new IntersectionObserver(([entry]) => {
      intersects = entry.isIntersecting;
      sync();
    });
    sync();
    observer.observe(node);
    document.addEventListener('visibilitychange', sync);
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', sync);
    };
  }, []);
  return ref;
}

/** Reticle layers move as complete SVGs: no per-frame React work, canvas
 * resampling, or animated SVG filter that repaints the whole reactor. */
export function VoiceLinkReticle({ link }: { link: VoiceLink }) {
  const ref = useLinkVisibility();
  return (
    <div ref={ref} className="vlink-reticle" data-phase={link.phase} aria-hidden="true">
      <svg className="vlink-reticle__sectors" viewBox="-300 -300 600 600">
        {STAGES.map((stage, i) => (
          <g
            key={stage}
            transform={`rotate(${i * 120 - 142})`}
            data-stage={i < link.step ? 'done' : i === link.step ? 'current' : 'waiting'}
          >
            <path d="M182 0A182 182 0 0 1-44 176.6" />
            <path d="M190 0h6 M-45.9 184.4l-1.5 5.8" />
          </g>
        ))}
      </svg>
      <svg key={link.phase} className="vlink-reticle__arrival" viewBox="-300 -300 600 600">
        <path d="M-200-30v-22h22 M200-30v-22h-22 M-200 30v22h22 M200 30v22h-22" />
      </svg>
      <svg className="vlink-reticle__pulse" viewBox="-300 -300 600 600">
        <circle r="152" />
      </svg>
      <svg className="vlink-reticle__orbit" viewBox="-300 -300 600 600">
        <circle r="167" strokeDasharray="35 315" />
        <circle r="172" strokeDasharray="2 41" className="vlink-reticle__ticks" />
        <circle cx="167" r="3" className="vlink-reticle__packet" />
        <circle cx="-167" r="3" className="vlink-reticle__packet" />
      </svg>
      <svg className="vlink-reticle__counter" viewBox="-300 -300 600 600">
        <path d="M-130-130h-16v16 M130-130h16v16 M130 130h16v-16 M-130 130h-16v-16" />
        <circle r="157" strokeDasharray="110 218.8" />
      </svg>
      <svg className="vlink-reticle__lock" viewBox="-300 -300 600 600">
        <path d="M-20 0l13 13L23-17" />
      </svg>
    </div>
  );
}

/** Real completed stages, never a time-derived progress bar. The moving
 * signal is intentionally indeterminate and stops the moment setup ends. */
export function VoiceLinkRail({ link, compact = false }: { link: VoiceLink; compact?: boolean }) {
  const ref = useLinkVisibility();
  const interrupted = link.phase === 'failed' || link.phase === 'stalled';
  return (
    <div
      ref={ref}
      className="vlink-rail"
      hidden={link.phase === 'idle'}
      data-phase={link.phase}
      data-compact={compact}
      data-voice-link={link.phase}
      role={compact ? 'status' : undefined}
      aria-live={compact ? 'polite' : undefined}
      aria-atomic={compact ? true : undefined}
    >
      <span className="vlink-beacon" aria-hidden="true">
        <svg viewBox="-18 -18 36 36">
          <circle r="14" strokeDasharray="19 10.32" />
          <circle r="9" strokeDasharray="2 7.42" />
        </svg>
        <i />
      </span>
      <span className="vlink-signal" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {!compact && interrupted ? (
        <span className="vlink-copy">
          <b>NUMPAD ENTER</b>
          <span>RETRY VOICE LINK</span>
        </span>
      ) : compact ? (
        <span className="vlink-copy">
          <b>{link.word}</b>
          <span>{link.detail}</span>
        </span>
      ) : (
        <ol className="vlink-stages" aria-label="Voice connection stages">
          {STAGES.map((stage, i) => (
            <li
              key={stage}
              data-stage={i < link.step ? 'done' : i === link.step ? 'current' : 'waiting'}
            >
              <i aria-hidden="true" />
              {stage}
              <span className="sr-only">
                {i < link.step ? ', complete' : i === link.step ? ', in progress' : ', waiting'}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
