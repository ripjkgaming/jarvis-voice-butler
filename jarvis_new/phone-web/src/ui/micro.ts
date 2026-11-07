/** Micro-interactions (PLAN.md §4.5) on the shared loop. They write only
 * transform/opacity of inner wrappers (never a morph element), or text. */
import { NOISE, prefersReducedMotion, scramble } from '@hud/ascii';
import { loop } from '../motion/loop';
import { EPS_PX, PRESETS, Spring } from '../motion/spring';

type Anim = { s: Spring; write: (x: number) => void; on: boolean };
const anims = new WeakMap<Element, Map<string, Anim>>();

function anim(el: HTMLElement, key: string, preset: { k: number; c: number }, init: number, write: (x: number) => void, eps = 0.0005) {
  let m = anims.get(el);
  if (!m) anims.set(el, (m = new Map()));
  let a = m.get(key);
  if (!a) m.set(key, (a = { s: new Spring(init, preset, eps), write, on: false }));
  return a;
}

function run(a: Anim) {
  if (a.on) return;
  a.on = true;
  loop.add((dt) => {
    const moving = a.s.advance(dt);
    a.write(a.s.x);
    if (!moving) a.on = false;
    return moving;
  });
}

/** Tile press: scale 1 → 0.96 on down, back on up (micro). */
export function press(el: HTMLElement | null, down: boolean) {
  if (!el || prefersReducedMotion()) return;
  const a = anim(el, 'press', PRESETS.micro, 1, (x) => (el.style.transform = x === 1 ? '' : `scale(${x.toFixed(4)})`));
  a.s.setTarget(down ? 0.96 : 1);
  run(a);
}

/** Failure nudge: translateX to +6 px then back to 0 (shake preset). */
export function shake(el: HTMLElement | null) {
  if (!el) return;
  if (prefersReducedMotion()) return;
  const a = anim(el, 'shake', PRESETS.shake, 0, (x) => (el.style.translate = Math.abs(x) < 0.01 ? '' : `${x.toFixed(2)}px 0`), EPS_PX);
  a.s.setTarget(6);
  run(a);
  loop.after(70, () => a.s.setTarget(0));
}

/** Lamp commit pulse: opacity 1 → 0.4 → 1 (micro). */
export function pulse(el: HTMLElement | null) {
  if (!el || prefersReducedMotion()) return;
  const a = anim(el, 'pulse', PRESETS.micro, 1, (x) => (el.style.opacity = x >= 0.999 ? '' : x.toFixed(3)));
  a.s.setTarget(0.4);
  run(a);
  loop.after(90, () => a.s.setTarget(1));
}

type Dec = { text: string; t0: number; ms: number; seed: number; last: string; at: number };
const decoding = new Map<Element, Dec>();
let decTask = false;
/** One loop task drives every active decode; noise refreshes at ~30 Hz and
 * unchanged text is never rewritten. */
function decodeTick() {
  const now = loop.now();
  for (const [el, d] of decoding) {
    const p = (now - d.t0) / d.ms;
    if (p >= 1) {
      if (d.last !== d.text) el.textContent = d.text;
      decoding.delete(el);
      continue;
    }
    if (now - d.at < 32) continue;
    d.at = now;
    const next = scramble(d.text, p, d.seed + Math.floor(p * 8));
    if (next !== d.last) {
      el.textContent = next;
      d.last = next;
    }
  }
  if (!decoding.size) decTask = false;
  return decTask;
}
/** Scramble `text` into `el` left to right over `ms` (ASCII decode). */
export function decode(el: Element | null, text: string, ms = 180, from = NOISE) {
  if (!el) return;
  void from;
  if (prefersReducedMotion() || !text) {
    decoding.delete(el);
    el.textContent = text;
    return;
  }
  decoding.set(el, { text, t0: loop.now(), ms, seed: Math.floor(Math.random() * 1000), last: '', at: -1e9 });
  if (!decTask) {
    decTask = true;
    loop.add(decodeTick);
  }
}

/** Decode every [data-decode] label inside a morphing element. */
export function decodeLabels(root: Element) {
  root.querySelectorAll<HTMLElement>('[data-decode]').forEach((x) => decode(x, x.dataset.decode || '', 180));
}
