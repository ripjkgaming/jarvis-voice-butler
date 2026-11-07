'use client';

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useDecode } from '@/components/ascii/use-decode';
import { useBridgeSysSnapshot } from '@/hooks/hud/use-bridge-sys';
import { prefersReducedMotion } from '@/lib/ascii';
import type { BridgeActivity } from '@/lib/bridge';
import { useAscii } from '@/lib/character/hooks';
import {
  MAX_RINGS,
  RingBank,
  SPAN_DEG,
  START_DEG,
  type Tier,
  capTasks,
  collectTasks,
  sweepArc,
} from '@/lib/ring-progress';
import { useSharedPoll } from '@/lib/shared-poll';
import { pt } from './geometry';
import css from './reactor-progress.module.css';

/**
 * Reactor progress: the one place every running job with progress is drawn.
 *
 *  - useReactorProgress() merges bridge /sys research jobs and /activity
 *    items (downloads, updates, builds, tools) into one list.
 *    (still feeds the standby badge.)
 *  - <ReactorProgress/> lives INSIDE the orb: one concentric segmented glyph
 *    ring per running task (up to 6, the 6th folds in the rest as "+N"), in
 *    the clear band just inside the rim, each tagged at the 12 o'clock gap.
 *    The centre disc reads out the focused task (cycles every 5 s). Motion
 *    is one rAF loop over lib/ring-progress.ts (critically damped, frame-rate
 *    independent, attribute writes only, no React renders per frame).
 *    Dev preview: `?ringdemo=N` (never in production builds).
 */

export type Job = {
  id: string;
  kind: string;
  tag: string;
  title: string;
  stage: string;
  /** 0-100, null = indeterminate. */
  pct: number | null;
  tone: 'cyan' | 'amber';
  started: number;
};

type Phase = 'idle' | 'active' | 'done' | 'leaving';
type View = { jobs: Job[]; phase: Phase; burst: number };

const EMPTY_JOBS: Job[] = [];
const IDLE_VIEW: View = { jobs: EMPTY_JOBS, phase: 'idle', burst: 0 };
const KIND_TAG: Record<string, string> = {
  download: 'DL',
  update: 'UPD',
  coding: 'CODE',
  research: 'RSCH',
  build: 'BUILD',
  task: 'TASK',
};
const CYCLE_S = 5;
const DONE_HOLD_MS = 1500;
const LEAVE_MS = 800;
const SHOW_AFTER_S = 1;

const clampPct = (v: number) => Math.max(0, Math.min(100, v));

/* One shared 1 Hz clock, running only while something is on screen. */
const tickSubs = new Set<() => void>();
let tickTimer: ReturnType<typeof setInterval> | null = null;
let tickSec = 0;
const nowSec = () => Math.floor(Date.now() / 1000);
function subscribeTick(cb: () => void) {
  if (tickSubs.size === 0) {
    tickSec = nowSec();
    tickTimer = setInterval(() => {
      tickSec = nowSec();
      tickSubs.forEach((f) => f());
    }, 1000);
  }
  tickSubs.add(cb);
  return () => {
    tickSubs.delete(cb);
    if (tickSubs.size === 0 && tickTimer) {
      clearInterval(tickTimer);
      tickTimer = null;
    }
  };
}
const noopSubscribe = () => () => {};
const getTick = () => tickSec;
const getNoTick = () => 0;

/** Merge every progress source into one view with a wake / done / leave
 *  lifecycle. Cheap to call from several components (polls are shared). */
export function useReactorProgress(): View & { focus: number } {
  const sys = useBridgeSysSnapshot();
  const raw = useSharedPoll<{ items?: BridgeActivity[] }>('/activity', 1000);
  const items = useMemo(() => (Array.isArray(raw?.items) ? raw.items : []), [raw]);
  const [view, setView] = useState<View>(IDLE_VIEW);

  const cands = useMemo<Job[]>(() => {
    const out: Job[] = [];
    const research = sys?.research ?? [];
    for (const r of research) {
      out.push({
        id: `r:${r.id}`,
        kind: 'research',
        tag: 'RSCH',
        title: r.title,
        stage: r.stage ?? '',
        pct: clampPct(Math.round(Number(r.progress) || 0)),
        tone: 'cyan',
        started: 0,
      });
    }
    for (const a of items) {
      if (a.status !== 'running') continue;
      if (a.kind === 'research' && research.some((r) => r.id === a.id || r.title === a.title))
        continue; // same job, already listed from /sys
      out.push({
        id: `a:${a.id}`,
        kind: a.kind,
        tag: KIND_TAG[a.kind] ?? 'TASK',
        title: a.title,
        stage: a.detail ?? '',
        pct: a.progress == null ? null : clampPct(Math.round(a.progress)),
        tone: a.kind === 'research' ? 'cyan' : 'amber',
        started: a.started,
      });
    }
    return out.length ? out : EMPTY_JOBS;
  }, [sys, items]);

  const needTick = cands.length > 0 || view.phase !== 'idle';
  const sec = useSyncExternalStore(
    needTick ? subscribeTick : noopSubscribe,
    needTick ? getTick : getNoTick,
    getNoTick
  );

  // Short-lived indeterminate tools would flicker the ring: wait a beat.
  const jobs = useMemo<Job[]>(() => {
    const live = cands.filter(
      (j) => j.pct !== null || (sec > 0 && sec - j.started >= SHOW_AFTER_S)
    );
    return live.length ? live : EMPTY_JOBS;
  }, [cands, sec]);

  // Finished = reported done, or vanished at 85%+ (research has no status).
  const statusRef = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    statusRef.current = new Map(items.map((a) => [`a:${a.id}`, a.status]));
  }, [items]);
  const prevRef = useRef<Job[]>(EMPTY_JOBS);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = jobs;
    const finished = (p: Job) => {
      const st = statusRef.current.get(p.id);
      if (st === 'done') return true;
      if (st === 'failed' || st === 'cancelled') return false;
      return (p.pct ?? 0) >= 85;
    };
    if (jobs.length > 0) {
      const gone = prev.some((p) => !jobs.some((j) => j.id === p.id) && finished(p));
      setView((v) => ({ jobs, phase: 'active', burst: v.burst + (gone ? 1 : 0) }));
      return;
    }
    if (prev.length === 0) return;
    const wasDone = prev.some(finished);
    const timers: ReturnType<typeof setTimeout>[] = [];
    const leave = () => {
      setView((v) => ({ ...v, phase: 'leaving' }));
      timers.push(setTimeout(() => setView((v) => ({ ...IDLE_VIEW, burst: v.burst })), LEAVE_MS));
    };
    if (wasDone) {
      setView((v) => ({
        jobs: prev.map((p) => ({ ...p, pct: 100 })),
        phase: 'done',
        burst: v.burst + 1,
      }));
      timers.push(setTimeout(leave, DONE_HOLD_MS));
    } else {
      setView((v) => ({ jobs: prev, phase: 'leaving', burst: v.burst }));
      leave();
    }
    return () => timers.forEach(clearTimeout);
  }, [jobs]);

  const n = view.jobs.length;
  const focus = n > 1 && view.phase === 'active' ? Math.floor(sec / CYCLE_S) % n : 0;
  return { ...view, focus };
}

/* -------------------------------------------------------------- rings */

/* All rings live in the clear band between the dashed ring (r~224) and the
 * rim glyph ring (r~259) of the ASCII reactor, in 880x600 viewBox units.
 * Ring geometry / lifecycle / easing is lib/ring-progress.ts (pure, tested);
 * this component only writes SVG attributes from one rAF loop. */
const VIEWBOX = '-440 -300 880 600';
/** Frame rotation: ring angle 0 sits START_DEG right of 12 o'clock. */
const FRAME = `rotate(${START_DEG - 90})`;
const CELLS = 87; // 87 x 4 deg glyph cells across the 348 deg span
// last gap absorbs the 12 deg label gap: 86 x 4 + 2.6 + 13.4 = 360
const CELL_DASH = Array.from({ length: CELLS }, (_, i) =>
  i === CELLS - 1 ? '2.6 13.4' : '2.6 1.4'
).join(' ');
const MILESTONES = [25, 50, 75, 100];
const PIPS = MILESTONES.map((m) => {
  const a = START_DEG + (m / 100) * SPAN_DEG - 90;
  const [x0, y0] = pt(224, a);
  const [x1, y1] = pt(254, a);
  return { m, x0, y0, x1, y1 };
});
const SLOTS = Array.from({ length: MAX_RINGS }, (_, i) => i);
const SPIN = ['▘', '▝', '▗', '▖'];

type Els = {
  g: SVGGElement | null;
  mask: SVGCircleElement | null;
  track: SVGCircleElement | null;
  glow: SVGCircleElement | null;
  fill: SVGCircleElement | null;
  trail: SVGCircleElement | null;
  flash: SVGCircleElement | null;
  head: SVGCircleElement | null;
  label: SVGTextElement | null;
};
const blankEls = (): Els => ({
  g: null,
  mask: null,
  track: null,
  glow: null,
  fill: null,
  trail: null,
  flash: null,
  head: null,
  label: null,
});

/** Last value written per attribute: unchanged values cost nothing. */
class Memo {
  private v = new Float64Array(24).fill(Number.NaN);
  set(el: Element | null, k: number, name: string, value: number, eps = 0.004): void {
    if (!el) return;
    const prev = this.v[k];
    if (prev === prev && Math.abs(prev - value) < eps) return;
    this.v[k] = value;
    el.setAttribute(name, value.toFixed(3));
  }
  changed(k: number, value: number, eps: number): boolean {
    const prev = this.v[k];
    if (prev === prev && Math.abs(prev - value) < eps) return false;
    this.v[k] = value;
    return true;
  }
  /** dasharray `len gap` + dashoffset `-start`. */
  dash(el: Element | null, k: number, len: number, start: number): void {
    if (!el) return;
    const l = len < 0 ? 0 : len > 360 ? 360 : len;
    const p = this.v[k];
    const q = this.v[k + 1];
    if (p === p && Math.abs(p - l) < 0.004 && Math.abs(q - start) < 0.004) return;
    this.v[k] = l;
    this.v[k + 1] = start;
    el.setAttribute('stroke-dasharray', `${l.toFixed(3)} ${(360 - l).toFixed(3)}`);
    el.setAttribute('stroke-dashoffset', (-start).toFixed(3));
  }
}
// attribute slots in Memo
const K = {
  op: 0,
  r: 1,
  w: 2,
  track: 3,
  glow: 5,
  fill: 7,
  trail: 9,
  flash: 11,
  head: 13,
  fop: 15,
  gw: 16,
};
const RINGS = ['mask', 'track', 'glow', 'fill', 'trail', 'flash', 'head'] as const;

type Readout = {
  on: boolean;
  done: boolean;
  tier: Tier;
  tag: string;
  title: string;
  stage: string;
  cells: string;
};

const IDLE_READOUT: Readout = {
  on: false,
  done: false,
  tier: 'cyan',
  tag: '',
  title: '',
  stage: '',
  cells: '',
};

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export function ReactorProgress() {
  const sys = useBridgeSysSnapshot();
  const raw = useSharedPoll<{ items?: BridgeActivity[] }>('/activity', 1000);
  const ascii = useAscii();
  const tasks = useMemo(
    () => capTasks(collectTasks(sys?.research, Array.isArray(raw?.items) ? raw.items : null)),
    [sys, raw]
  );

  const [bank] = useState(() => new RingBank());
  const [readout, setReadout] = useState<Readout>(IDLE_READOUT);
  const [burst, setBurst] = useState(0);
  const els = useRef<Els[]>(SLOTS.map(blankEls));
  const memos = useRef<Memo[]>(SLOTS.map(() => new Memo()));
  const pips = useRef<(SVGLineElement | null)[]>([]);
  const pctEl = useRef<HTMLSpanElement | null>(null);
  const kick = useRef<() => void>(() => {});
  const asciiRef = useRef(ascii);
  asciiRef.current = ascii;

  // Real data in. Dev-only preview: `?ringdemo=N` swaps in synthetic tasks.
  const demoN = () =>
    process.env.NODE_ENV !== 'production'
      ? Number(new URLSearchParams(window.location.search).get('ringdemo')) || 0
      : 0;
  useEffect(() => {
    if (demoN() > 0) return;
    bank.sync(tasks, performance.now());
    kick.current();
  }, [bank, tasks]);

  useEffect(() => {
    const n = demoN();
    if (n <= 0) return;
    let timer: ReturnType<typeof setInterval> | undefined;
    let alive = true;
    void import('@/lib/ring-progress-demo').then(({ demoTasks }) => {
      if (!alive) return;
      const push = () => {
        bank.sync(demoTasks(performance.now(), n), performance.now());
        kick.current();
      };
      push();
      timer = setInterval(push, 250);
    });
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [bank]);

  useEffect(() => {
    const sw = { head: 0, len: 0 };
    const st = {
      raf: 0,
      last: 0,
      rev: -1,
      focus: -2,
      count: -1,
      burst: 0,
      pct: -1,
      cells: '',
      tiers: ['', '', '', '', '', ''],
      pctText: '',
      phase: '',
    };
    const frame = (now: number) => {
      st.raf = 0;
      const dt = st.last ? now - st.last : 16;
      st.last = now;
      const reduced = prefersReducedMotion();
      const alive = bank.step(now, dt, reduced);
      if (reduced) {
        sw.head = 0;
        sw.len = 90;
      } else sweepArc(now, sw);

      for (const s of bank.slots) {
        const e = els.current[s.idx];
        const m = memos.current[s.idx];
        if (!e.g) continue;
        if (!s.shown || s.phase === 'free') {
          m.set(e.g, K.op, 'opacity', 0, 0.001);
          continue;
        }
        if (s.tier !== st.tiers[s.idx]) {
          st.tiers[s.idx] = s.tier;
          e.g.setAttribute('data-tier', s.tier);
        }
        m.set(e.g, K.op, 'opacity', s.op, 0.003);
        if (s.op <= 0.003) continue;
        if (m.changed(K.r, s.r, 0.01)) {
          const rs = s.r.toFixed(3);
          for (const k of RINGS) e[k]?.setAttribute('r', rs);
        }
        m.set(e.mask, K.w, 'stroke-width', s.w + 0.5, 0.01);

        const rev = s.rev.x < 0 ? 0 : s.rev.x > 1 ? 1 : s.rev.x;
        const span = SPAN_DEG * rev;
        m.dash(e.track, K.track, span, 0);

        const f = Math.min(span, (s.fill.x < 0 ? 0 : s.fill.x > 1 ? 1 : s.fill.x) * SPAN_DEG);
        const ind = s.indet;
        const tail = sw.head - sw.len;
        const start = ind * tail;
        const len = lerp(f, sw.len, ind);
        m.dash(e.fill, K.fill, len, start);
        // soft halo: same arc, wider; blooms on completion
        m.set(e.glow, K.gw, 'stroke-width', s.w * 2.4 + s.flash * s.w * 3, 0.02);
        m.set(e.glow, K.fop, 'opacity', 0.16 + s.flash * 0.6, 0.004);
        m.dash(e.glow, K.glow, len, start);
        const tl = Math.min(len, 22);
        m.dash(e.trail, K.trail, tl, start + len - tl);
        m.dash(e.flash, K.flash, s.flash > 0.01 ? span : 0, 0);
        const headAt = start + len;
        const headWrap = ((headAt % 360) + 360) % 360;
        m.dash(e.head, K.head, len > 0.4 && headWrap < SPAN_DEG ? 0.01 : 0, headAt);
        if (e.label && s.rev.x > 0.2) {
          e.label.setAttribute('y', (-s.r + Math.min(5, s.w * 1.6) * 0.36).toFixed(2));
          e.label.setAttribute('font-size', Math.min(5.4, Math.max(3.4, s.w * 1.6)).toFixed(2));
        }
      }

      // focused ring drives the centre numerals + milestone pips
      const fs = bank.focus >= 0 ? bank.slots[bank.focus] : null;
      let text = '';
      if (fs) {
        if (fs.pct === null && fs.phase === 'running')
          text = SPIN[Math.floor(now / 110) % SPIN.length];
        else text = `${Math.round(Math.min(1, Math.max(0, fs.fill.x)) * 100)}%`;
      }
      if (text !== st.pctText && pctEl.current) {
        st.pctText = text;
        pctEl.current.textContent = text;
      }
      const fx = fs ? fs.fill.x * 100 : 0;
      PIPS.forEach(({ m }, i) =>
        pips.current[i]?.setAttribute('opacity', fs && fx >= m - 0.5 ? '1' : '0.28')
      );
      // per-ring labels: only text that changed
      for (const s of bank.slots) {
        const lab = els.current[s.idx].label;
        if (!lab || !s.shown) continue;
        const known = s.pct !== null;
        const pc = known ? Math.round(Math.min(1, Math.max(0, s.fill.x)) * 100) : -1;
        const key = `${s.id}|${pc}|${asciiRef.current ? 1 : 0}`;
        if (lab.dataset.k !== key) {
          lab.dataset.k = key;
          const tag = asciiRef.current ? `[${s.tag}]` : s.tag;
          lab.textContent = known ? `${tag} ${pc}` : tag;
        }
      }

      // React only hears about identity changes, never about frames
      if (bank.burst !== st.burst) {
        st.burst = bank.burst;
        setBurst(bank.burst);
      }
      const cells = bank.slots
        .filter((s) => s.shown && s.phase !== 'leaving')
        .map((s) => `${s.tier[0]}${s.idx === bank.focus ? '*' : ''}`)
        .join(',');
      const phase = fs ? fs.phase : '';
      if (
        bank.rev !== st.rev ||
        bank.focus !== st.focus ||
        cells !== st.cells ||
        phase !== st.phase
      ) {
        st.rev = bank.rev;
        st.focus = bank.focus;
        st.cells = cells;
        st.phase = phase;
        setReadout(
          bank.count > 0 && fs
            ? {
                on: true,
                done: fs.phase === 'completing' || fs.phase === 'flash',
                tier: fs.tier,
                tag: fs.tag,
                title: fs.title,
                stage: fs.stage,
                cells,
              }
            : { ...IDLE_READOUT, on: bank.count > 0, cells }
        );
      }

      if (alive && !document.hidden) st.raf = requestAnimationFrame(frame);
      else st.last = 0;
    };
    kick.current = () => {
      if (!st.raf && !document.hidden) st.raf = requestAnimationFrame(frame);
    };
    const onVis = () => {
      if (document.hidden) {
        if (st.raf) cancelAnimationFrame(st.raf);
        st.raf = 0;
        st.last = 0;
      } else kick.current();
    };
    document.addEventListener('visibilitychange', onVis);
    kick.current();
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      if (st.raf) cancelAnimationFrame(st.raf);
      st.raf = 0;
    };
  }, [bank]);

  const title = useDecode(readout.title, 420);
  const stageRaw = !readout.on
    ? ''
    : readout.done
      ? 'COMPLETE'
      : (readout.stage || 'IN PROGRESS').toUpperCase();
  const stage = useDecode(stageRaw, 380);
  const tag = readout.tag ? (ascii ? `[${readout.tag}]` : readout.tag) : '';
  const setEl =
    (i: number, k: keyof Els) =>
    (n: Element | null): void => {
      (els.current[i] as Record<string, Element | null>)[k] = n;
    };
  const legend = readout.cells ? readout.cells.split(',') : [];

  return (
    <div
      className={css.ring}
      data-ix-layer="7"
      data-on={readout.on ? 'true' : undefined}
      data-done={readout.done ? 'true' : undefined}
      data-tier={readout.tier}
    >
      <svg className={css.ghost} viewBox={VIEWBOX} aria-hidden="true">
        <g transform={FRAME}>
          <circle r={240} pathLength={360} strokeDasharray={CELL_DASH} className={css.ghostTrack} />
        </g>
        {PIPS.map(({ m, x0, y0, x1, y1 }) => (
          <line key={m} x1={x0} y1={y0} x2={x1} y2={y1} className={css.ghostPip} />
        ))}
      </svg>
      <svg className={css.live} viewBox={VIEWBOX} aria-hidden="true">
        <defs>
          {SLOTS.map((i) => (
            <mask
              key={i}
              id={`rp-seg-${i}`}
              maskUnits="userSpaceOnUse"
              x="-340"
              y="-340"
              width="680"
              height="680"
            >
              <circle
                ref={setEl(i, 'mask')}
                r={240}
                fill="none"
                stroke="#fff"
                strokeWidth={6}
                pathLength={360}
                strokeDasharray={CELL_DASH}
                transform={FRAME}
              />
            </mask>
          ))}
        </defs>
        {SLOTS.map((i) => (
          <g key={i} ref={setEl(i, 'g')} opacity="0" className={css.slot}>
            <g transform={FRAME} fill="none">
              <circle
                ref={setEl(i, 'glow')}
                r={240}
                pathLength={360}
                strokeDasharray="0 360"
                className={css.glow}
              />
            </g>
            <g mask={`url(#rp-seg-${i})`}>
              <g transform={FRAME} fill="none">
                <circle
                  ref={setEl(i, 'track')}
                  r={240}
                  pathLength={360}
                  strokeDasharray="0 360"
                  className={css.track}
                />
                <circle
                  ref={setEl(i, 'fill')}
                  r={240}
                  pathLength={360}
                  strokeDasharray="0 360"
                  className={css.fill}
                />
                <circle
                  ref={setEl(i, 'trail')}
                  r={240}
                  pathLength={360}
                  strokeDasharray="0 360"
                  className={css.trail}
                />
                <circle
                  ref={setEl(i, 'flash')}
                  r={240}
                  pathLength={360}
                  strokeDasharray="0 360"
                  className={css.flashRing}
                />
              </g>
            </g>
            <g transform={FRAME} fill="none" strokeLinecap="round">
              <circle
                ref={setEl(i, 'head')}
                r={240}
                pathLength={360}
                strokeDasharray="0 360"
                className={css.head}
              />
            </g>
            <text
              ref={setEl(i, 'label')}
              x="0"
              y="-240"
              textAnchor="middle"
              className={css.label}
            />
          </g>
        ))}
        {PIPS.map(({ m, x0, y0, x1, y1 }, i) => (
          <line
            key={m}
            ref={(n) => {
              pips.current[i] = n;
            }}
            x1={x0}
            y1={y0}
            x2={x1}
            y2={y1}
            opacity="0.28"
            className={css.pip}
          />
        ))}
      </svg>
      <div
        className={css.disc}
        role="progressbar"
        aria-hidden={readout.on ? undefined : true}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={readout.on ? `${readout.title}: ${readout.stage}` : 'Progress'}
      >
        <span className={css.tag}>{tag}</span>
        <span ref={pctEl} className={css.pct} />
        <span className={css.title}>{title}</span>
        <span className={css.stage}>{stage}</span>
        {legend.length > 1 ? (
          <span className={css.legend} aria-hidden="true">
            {legend.map((c, i) => (
              <i
                key={i}
                data-tier={c[0] === 'c' ? 'cyan' : c[0] === 'i' ? 'ice' : 'amber'}
                data-focus={c.endsWith('*') ? 'true' : undefined}
              />
            ))}
          </span>
        ) : null}
      </div>
      {burst > 0 ? (
        <span key={`b${burst}`} className={css.burst}>
          <i className={css.burstFlash} />
          <i className={css.burstA} />
          <i className={css.burstB} />
        </span>
      ) : null}
    </div>
  );
}
