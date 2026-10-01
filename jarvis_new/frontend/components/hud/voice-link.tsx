'use client';

import { useEffect, useRef } from 'react';
import type { VoiceLink } from '@/lib/voice-link';
import './voice-link.css';

const STAGES = ['REQUEST', 'VOICE RELAY', 'AGENT LINK'];

/** Reticle layers move as complete SVGs: no per-frame React work, canvas
 * resampling, or animated SVG filter that repaints the whole reactor. */
export function VoiceLinkReticle({ link }: { link: VoiceLink }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new IntersectionObserver(([entry]) => {
      node.dataset.visible = String(entry.isIntersecting);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return (
    <div ref={ref} className="vlink-reticle" data-phase={link.phase} aria-hidden="true">
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
  if (link.phase === 'idle') return null;
  return (
    <div
      className="vlink-rail"
      data-phase={link.phase}
      data-compact={compact}
      data-voice-link={link.phase}
      role="status"
      aria-live="polite"
    >
      <span className="vlink-signal" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {compact ? (
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
