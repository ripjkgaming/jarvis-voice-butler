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

/** Continuous blue acquisition across real setup stages, without a timed finish.
 * Four bounded moving SVG layers, no per-frame JS or full-reactor filters. */
export function VoiceLinkReticle({ link }: { link: VoiceLink }) {
  const ref = useLinkVisibility();
  return (
    <div ref={ref} className="vlink-reticle" data-phase={link.phase} aria-hidden="true">
      <div className="vlink-reticle__veil" />
      <svg className="vlink-reticle__sectors" viewBox="-300 -300 600 600">
        <circle r="244" className="vlink-reticle__hairline" />
        <circle r="236" strokeDasharray="1 11.36" className="vlink-reticle__ticks" />
        {STAGES.map((stage, i) => (
          <g
            key={stage}
            transform={`rotate(${i * 120 - 142})`}
            data-stage={i < link.step ? 'done' : i === link.step ? 'current' : 'waiting'}
          >
            <path d="M214 0A214 214 0 0 1-53 207" />
            <path d="M222 0h10 M-55 215l-2.5 9.7" />
            <circle cx="214" cy="0" r="3" className="vlink-reticle__node" />
          </g>
        ))}
        <path
          className="vlink-reticle__hairline"
          d="M-260-75h24 M260-75h-24 M-260 75h24 M260 75h-24 M0-269v15 M0 269v-15"
        />
      </svg>
      <svg className="vlink-reticle__orbit" viewBox="-300 -300 600 600">
        <circle r="194" strokeDasharray="86 218.73" />
        <circle r="187" strokeDasharray="14 377.66" className="vlink-reticle__carrier" />
        <path d="M194-11v22 M-105-164l-16-12 M-105 164l-16 12" />
        <circle r="201" strokeDasharray="2 40.1" className="vlink-reticle__ticks" />
        <path d="M194 0h12 M-194 0h-12 M0 194v12 M0-194v-12" />
      </svg>
      <svg className="vlink-reticle__counter" viewBox="-300 -300 600 600">
        <path d="M-114-114h-27v27 M114-114h27v27 M114 114h27v-27 M-114 114h-27v-27" />
        <circle r="157" strokeDasharray="110 218.8" />
      </svg>
      <svg className="vlink-reticle__pulse" viewBox="-300 -300 600 600">
        <circle r="150" />
        <circle r="155" className="vlink-reticle__ticks" />
      </svg>
      <svg className="vlink-reticle__scan" viewBox="-300 -300 600 600">
        <path d="M-142 0h284 M-104-5h208 M-62 5H62" />
        <path d="M-148-8v16 M148-8v16" />
      </svg>
      <div className="vlink-reticle__identity">
        <span>NEURAL INTERFACE</span>
        <strong>J.A.R.V.I.S.</strong>
        <span>{link.phase === 'ready' ? 'VOICE LINK ESTABLISHED' : 'ACQUIRING VOICE LINK'}</span>
        <ol className="vlink-reticle__channels">
          {['REQUEST', 'RELAY', 'AGENT'].map((label, i) => (
            <li
              key={label}
              data-stage={i < link.step ? 'done' : i === link.step ? 'current' : 'waiting'}
            >
              <i />
              <span>{label}</span>
            </li>
          ))}
        </ol>
      </div>
      <svg className="vlink-reticle__lock" viewBox="-300 -300 600 600">
        <path d="M-18 88l12 12 24-27" />
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
          <b>{link.word}</b>
          <span>{link.detail}</span>
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
      {interrupted && link.retry && (
        <button type="button" className="vlink-retry" onClick={link.retry}>
          RETRY <span aria-hidden="true">↗</span>
        </button>
      )}
    </div>
  );
}

/** One-second wake acknowledgement; it never holds back real readiness. */
export function VoiceActivationBurst({ activation }: { activation?: number }) {
  const ref = useLinkVisibility();
  return (
    <div
      ref={ref}
      className="vlink-wake"
      data-activation={activation || undefined}
      aria-hidden="true"
    >
      {activation ? (
        <svg key={activation} viewBox="-300 -300 600 600">
          <g className="vlink-wake__ring">
            <circle r="226" />
            <circle r="232" strokeDasharray="16 8" />
          </g>
          <g className="vlink-wake__brackets">
            <path d="M-190-120v-65h65 M190-120v-65h-65 M-190 120v65h65 M190 120v65h-65" />
          </g>
          <g className="vlink-wake__label">
            <rect x="-125" y="81" width="250" height="36" />
            <text x="0" y="105" textAnchor="middle">
              WAKE RECEIVED
            </text>
          </g>
        </svg>
      ) : null}
    </div>
  );
}
