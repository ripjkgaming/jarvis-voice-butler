/** The mark SVG (PLAN.md §3): 1 ring circle (+ its glow copy), 4 satellite
 * <g>s and 1 core <path>, in a 108 viewBox centred on the mark. Every
 * instance is driven by the one shared MarkModel (motion/mark.ts). */
import { useEffect, useRef } from 'preact/hooks';
import { loop } from '../motion/loop';
import { Ignition, MarkModel, MARK, type MarkView } from '../motion/mark';
import { call } from '../store';
import type { Glyph } from '../store';

const SAT = 'M0 -2.6 L1.3 0 L0 2.6 L-1.3 0Z';
const R = MARK.ring_radius;

export const markModel = new MarkModel(false, 'star');
let running = false;
const task = (dt: number) => {
  const c = call.value;
  const live = c.phase === 'live';
  const moving = markModel.step(dt, { agent: c.agent, micRms: live ? c.micRms : 0, playRms: live ? c.playRms : 0 });
  if (!moving || document.hidden) running = false;
  return running;
};
export function kickMark() {
  if (running) return;
  running = true;
  loop.add(task);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) kickMark();
});
export const ignition = new Ignition(markModel, (ms, fn) => loop.after(ms, fn), kickMark);

const ghosts = new Set<{ main: SVGSVGElement; ghost: SVGSVGElement }>();
/** Change glyph; reduced motion swaps with a 120 ms crossfade (two stacked SVGs). */
export function showGlyph(glyph: Glyph, guest: boolean) {
  if (markModel.reduced && (glyph !== markModel.glyph || guest !== markModel.guest)) {
    for (const g of ghosts) {
      g.ghost.innerHTML = g.main.innerHTML;
      g.ghost.style.transition = 'none';
      g.ghost.style.opacity = '1';
      g.main.style.transition = 'none';
      g.main.style.opacity = '0';
    }
    markModel.setGlyph(glyph, guest, true);
    markModel.write({ agent: 'idle', micRms: 0, playRms: 0 });
    requestAnimationFrame(() => {
      for (const g of ghosts) {
        g.ghost.style.transition = 'opacity 120ms linear';
        g.main.style.transition = 'opacity 120ms linear';
        g.ghost.style.opacity = '0';
        g.main.style.opacity = '1';
      }
    });
    return;
  }
  markModel.setGlyph(glyph, guest);
  kickMark();
}

export function Mark({ morph, cls }: { morph: string; cls: string }) {
  const svg = useRef<SVGSVGElement>(null);
  const ghost = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const root = svg.current!;
    const view: MarkView = {
      core: root.querySelector('.mk-core') as SVGPathElement,
      coreGroup: root.querySelector('.mk-coreg') as SVGGElement,
      satGroup: root.querySelector('.mk-satg') as SVGGElement,
      sats: [...root.querySelectorAll('.mk-sat')] as SVGGElement[],
      rings: [...root.querySelectorAll('.mk-ring')] as SVGCircleElement[],
      page: root.closest('[data-page]'),
    };
    markModel.views.add(view);
    const pair = { main: root, ghost: ghost.current! };
    ghosts.add(pair);
    markModel.write({ agent: 'idle', micRms: 0, playRms: 0 });
    kickMark();
    return () => {
      markModel.views.delete(view);
      ghosts.delete(pair);
    };
  }, []);
  return (
    <div class={`m mark ${cls}`} data-morph={morph} aria-hidden="true">
      <svg ref={ghost} class="mk-ghost" viewBox="-54 -54 108 108" />
      <svg ref={svg} class="mk-main" viewBox="-54 -54 108 108">
        <circle class="mk-ring mk-glow" r={R} fill="none" stroke-width="7" opacity="0.16" />
        <circle class="mk-ring" r={R} fill="none" stroke-width="2.4" />
        <circle r={R - 3.8} fill="none" stroke="#e8fbff" stroke-width="0.6" opacity="0.35" />
        <g class="mk-satg">
          {[0, 1, 2, 3].map((i) => (
            <g key={i} class="mk-sat">
              <path d={SAT} fill="#5fe3ff" />
            </g>
          ))}
        </g>
        <g class="mk-coreg">
          <path class="mk-core" stroke-width="0.6" stroke-linejoin="round" />
        </g>
      </svg>
    </div>
  );
}
