/** Damped spring, mass 1: a = −k(x − target) − c·v (PLAN.md §4.1).
 * Semi-implicit Euler at a fixed 1/240 s step; a frame consumes its elapsed
 * time in whole substeps (remainder carried), clamped to 64 ms. setTarget only
 * moves the target: position and velocity are never reset, which is the whole
 * interruptibility contract. */

export const DT = 1 / 240;
export const MAX_FRAME = 0.064;

export type Preset = { k: number; c: number };
export const PRESETS = {
  page: { k: 380, c: 39 },
  mark: { k: 260, c: 32 },
  micro: { k: 700, c: 42 },
  shake: { k: 900, c: 18 },
} satisfies Record<string, Preset>;

export const EPS_NORM = 0.0005;
export const EPS_PX = 0.05;

export class Spring {
  x: number;
  v = 0;
  target: number;
  k: number;
  c: number;
  eps: number;
  private carry = 0;

  constructor(x: number, preset: Preset, eps = EPS_NORM) {
    this.x = x;
    this.target = x;
    this.k = preset.k;
    this.c = preset.c;
    this.eps = eps;
  }

  get settled() {
    return Math.abs(this.x - this.target) < this.eps && Math.abs(this.v) < 10 * this.eps;
  }

  setTarget(t: number) {
    this.target = t;
  }

  /** Jump without motion (layout boot, reduced motion). */
  snap(t: number) {
    this.x = this.target = t;
    this.v = 0;
    this.carry = 0;
  }

  /** Advance by `seconds` of wall time. Returns true while still moving. */
  advance(seconds: number) {
    if (this.settled) {
      this.x = this.target;
      this.v = 0;
      return false;
    }
    let left = Math.min(Math.max(seconds, 0), MAX_FRAME) + this.carry;
    while (left >= DT - 1e-12) {
      const a = -this.k * (this.x - this.target) - this.c * this.v;
      this.v += a * DT;
      this.x += this.v * DT;
      left -= DT;
    }
    this.carry = left;
    if (this.settled) {
      this.x = this.target;
      this.v = 0;
      this.carry = 0;
      return false;
    }
    return true;
  }
}
