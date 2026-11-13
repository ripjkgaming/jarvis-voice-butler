'use client';

import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

/** Holographic projection stage for Jarvis 3D objects (Iron Man style).
 *  Pure presentation: feed it the /model3d/state snapshot + a glb fetcher.
 *  - build-up: glyph-block scan front sweeps up the object (edges lead faces),
 *    de-rez on dismiss is the same sweep reversed;
 *  - edit refresh: glb change morphs live (new object scans in over the old one);
 *  - floats + orbits over a projector disc (ASCII glyph rings), faint light
 *    cone, flicker/glitch, drifting glyph particles; transparent canvas, no chrome;
 *  - every voice command (state.cmd/cmd_seq) is echoed visually (tint, pulse, tag).
 *  No pointer events anywhere: voice only. */

export type HoloState = {
  open?: boolean;
  has_glb?: boolean;
  rotate?: boolean;
  speed?: number;
  dir?: number;
  /** manual orientation offsets in degrees: pitch = up/down tilt (X), yaw = sideways turn (Y), roll = clockwise/anticlockwise (Z) */
  pitch?: number;
  yaw?: number;
  roll?: number;
  /** axis the auto-rotation spins about */
  spin_axis?: 'x' | 'y' | 'z';
  zoom?: number;
  wire?: boolean;
  explode?: boolean;
  scan?: boolean;
  shape4d?: boolean;
  /** physics simulation playback (src/physics_tools.py): play file from /model3d/sim, frame 0 at sim_start (epoch s) */
  sim?: boolean;
  sim_mtime?: number;
  sim_start?: number;
  shape4d_mtime?: number;
  rot4d?: boolean;
  plane4d?: string;
  cmd?: string;
  cmd_seq?: number;
  slug?: string | null;
  version?: number;
  glb_mtime?: number;
  fx?: Record<string, Fx>;
  parts?: { id: string; role: string; extras?: Record<string, unknown> }[];
  caps?: { id: string; role: string; parts: string[] }[];
  /** part ids named by commands.json steps (excl. reset): the only parts kept as separate meshes */
  movable?: string[];
  /** last voice command heard + Jarvis's reply (caption), say_ts = epoch seconds */
  heard?: string;
  reply?: string;
  say_ts?: number;
  seq?: number;
};

/** Per-part voice state (src/model3d_parts.py). */
export type Fx = {
  on?: boolean;
  level?: number;
  color?: string;
  open?: number;
  spin?: number;
  swing?: number;
  turn?: number;
  slide?: number;
  wave?: boolean;
};

type Props = {
  state: HoloState | null;
  fetchGlb: () => Promise<ArrayBuffer | null>;
  fetchShape?: () => Promise<Shape4D | null>;
  fetchSim?: () => Promise<SimData | null>;
};

/** Teaching label on the simulation: point, dimension line, angle arc, or a tag that follows a body. */
export type SimMark = {
  t: 'pt' | 'line' | 'arc' | 'tag';
  f: number;
  text?: string;
  p?: number[];
  a?: number[] | string;
  b?: number[];
  c?: string;
  c0?: number[];
  u?: number[];
  v?: number[];
  r?: number;
  col?: string;
  dash?: boolean;
  arrow?: boolean;
  low?: boolean;
  up?: number;
  body?: number;
};

export type SimData = {
  name?: string;
  fps: number;
  dur: number;
  rate?: number;
  /** seconds the stage disc takes to morph into the floor grid, ending at sim_start */
  morph?: number;
  marks?: SimMark[];
  bodies: { label: string; r: number; color: string; fixed: boolean; trail?: boolean }[];
  floor: number | null;
  box: number | null;
  ctr: number[];
  frames: number[][];
};

export type Shape4D = { name?: string; verts: number[][]; edges: number[][] };

const CYAN = new THREE.Color('#5fe3ff');
const AMBER = new THREE.Color('#ffb347');
const ICE = new THREE.Color('#d8f6ff');

const CMD_TINT: Record<string, THREE.Color> = {
  HOLD: AMBER,
  EXPLODE: AMBER,
  ASSEMBLE: ICE,
  SCAN: AMBER,
  'SCAN OFF': ICE,
  REVERSE: ICE,
  FASTER: ICE,
  SLOWER: ICE,
  RESET: ICE,
  WIREFRAME: CYAN,
  SOLID: ICE,
  'ZOOM +': ICE,
  'ZOOM -': ICE,
};

// ---------------------------------------------------------------- shaders

const NOISE = /* glsl */ `
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
`;

const MODEL_VERT = /* glsl */ `
uniform float uTime;
uniform float uGlitch;
uniform float uExpV;
uniform float uExpMag;
uniform float uIdleA;
uniform vec3 uIdleP;
attribute vec3 aDir;
attribute float aW;
attribute vec3 aCol;
varying vec3 vW;
varying float vDepth;
varying vec3 vC;
void main(){
  vDepth = aW;
  vC = aCol;
  // idle: every piece (island/chunk, own aDir) drifts + wobbles on its own phase and rate; uIdleP = per-object signature
  float ph = dot(aDir, vec3(12.9898, 78.233, 37.719)) + uIdleP.x;
  float fr = 0.5 + 0.9 * fract(sin(ph * 4.37) * 4375.5453) + uIdleP.y;
  vec3 idle = aDir * (uIdleA * sin(uTime * fr + ph))
            + cross(aDir, vec3(0.0, 1.0, 0.0)) * (uIdleA * 0.6 * sin(uTime * fr * 0.7 + ph * 1.7 + uIdleP.z));
  vec4 w = modelMatrix * vec4(position + aDir * (uExpMag * uExpV) + idle, 1.0);
  float row = floor(w.y * 14.0 + uTime * 6.0);
  float j = step(0.86, fract(sin(row * 91.7) * 4375.5));
  w.x += uGlitch * j * 0.09 * sin(row * 13.0);
  vW = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}
`;

const REVEAL = /* glsl */ `
uniform float uTime, uReveal, uInvert, uSolid, uY0, uY1, uFlick, uScan, uLead;
uniform vec3 uTint;
varying vec3 vW;
${NOISE}
// returns d: >0 revealed. h = normalised height.
float revealD(float h, float lead){
  float f = uReveal * 1.32 - 0.16;
  float d = mix(f - h, h - f, uInvert);
  return d + lead;
}
`;

const COLV = /* glsl */ `
varying vec3 vC;
vec3 baseCol(){ return dot(vC, vC) > 0.0005 ? vC : uTint; }
`;

const FACE_FRAG = /* glsl */ `
${REVEAL}
${COLV}
void main(){
  float h = clamp((vW.y - uY0) / max(uY1 - uY0, 1e-3), 0.0, 1.0);
  float d = revealD(h, 0.0);
  if (d < 0.0) discard;
  vec2 cell = floor(gl_FragCoord.xy / 5.0);
  float keep = d / 0.11;
  if (keep < 1.0 && hash(cell + floor(uTime * 14.0)) > keep) discard;
  float front = 1.0 - smoothstep(0.0, 0.05, d);

  vec3 n = normalize(cross(dFdx(vW), dFdy(vW)));
  vec3 v = normalize(cameraPosition - vW);
  float fres = pow(1.0 - abs(dot(n, v)), 2.0);
  float lines = 0.5 + 0.5 * sin(vW.y * 110.0 - uTime * 3.0);
  float band = smoothstep(0.82, 1.0, sin(vW.y * 6.0 - uTime * 1.1) * 0.5 + 0.5);

  float a = mix(0.035, 0.2, uSolid) + fres * mix(0.34, 0.5, uSolid) + lines * mix(0.03, 0.09, uSolid) + band * 0.09;
  vec3 bc = baseCol();
  vec3 col = mix(bc * 0.45, bc, clamp(fres * 1.4, 0.0, 1.0));
  col = mix(col, vec3(0.88, 0.97, 1.0), front * 0.85);
  a += front * 0.9;

  float sy = uY0 + (uY1 - uY0) * fract(uTime * 0.33);
  float sc = exp(-abs(vW.y - sy) * 26.0) * uScan;
  col = mix(col, vec3(1.0, 0.72, 0.3), clamp(sc, 0.0, 1.0));
  a += sc * 1.1;

  a *= uFlick;
  gl_FragColor = vec4(min(col, vec3(1.0)), clamp(a, 0.0, 1.0));
}
`;

const EDGE_FRAG = /* glsl */ `
${REVEAL}
${COLV}
void main(){
  float h = clamp((vW.y - uY0) / max(uY1 - uY0, 1e-3), 0.0, 1.0);
  float d = revealD(h, uLead);
  if (d < 0.0) discard;
  vec2 cell = floor(gl_FragCoord.xy / 3.0);
  float keep = d / 0.07;
  if (keep < 1.0 && hash(cell + floor(uTime * 20.0)) > keep) discard;
  float front = 1.0 - smoothstep(0.0, 0.06, d);
  float dim = mix(1.0, 0.62, uSolid);
  vec3 col = mix(baseCol(), vec3(0.9, 0.98, 1.0), 0.18 + front * 0.7);
  float sy = uY0 + (uY1 - uY0) * fract(uTime * 0.33);
  float sc = exp(-abs(vW.y - sy) * 26.0) * uScan;
  col = mix(col, vec3(1.0, 0.75, 0.32), clamp(sc, 0.0, 1.0));
  float a = (0.78 * dim + sc) * uFlick;
  gl_FragColor = vec4(min(col, vec3(1.0)), clamp(a, 0.0, 1.0));
}
`;

// 4D wireframe: edges nearer in w glow ice-white, farther in w fade to amber
const EDGE4_FRAG = /* glsl */ `
${REVEAL}
varying float vDepth;
void main(){
  float h = clamp((vW.y - uY0) / max(uY1 - uY0, 1e-3), 0.0, 1.0);
  float d = revealD(h, uLead);
  if (d < 0.0) discard;
  float front = 1.0 - smoothstep(0.0, 0.06, d);
  float k = clamp(vDepth * 0.5 + 0.5, 0.0, 1.0);
  vec3 col = mix(vec3(1.0, 0.72, 0.3), mix(uTint, vec3(0.92, 0.99, 1.0), 0.45), k);
  col = mix(col, vec3(0.95, 1.0, 1.0), front * 0.7);
  float a = (0.62 + 0.38 * k) * uFlick;
  gl_FragColor = vec4(min(col, vec3(1.0)), clamp(a, 0.0, 1.0));
}
`;

const DISC_VERT = /* glsl */ `
varying vec2 vP;
void main(){ vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

const DISC_FRAG = /* glsl */ `
uniform float uTime, uPower, uSweep, uPulseR, uPulseA;
uniform vec3 uTint;
varying vec2 vP;
float ring(float r, float c, float w){ return smoothstep(w, 0.0, abs(r - c)); }
void main(){
  float r = length(vP) / 2.3;
  if (r > 1.0) discard;
  float ang = atan(vP.y, vP.x);
  float t = ang / 6.28318;
  float a = (1.0 - r) * (1.0 - r) * 0.24;
  a += ring(r, 0.985, 0.012) * 0.95 + ring(r, 0.9, 0.006) * 0.55 + ring(r, 0.52, 0.006) * 0.4 + ring(r, 0.2, 0.006) * 0.45;
  float ticks = step(0.5, fract((t + uTime * 0.02) * 144.0)) * step(0.915, r) * step(r, 0.965);
  a += ticks * 0.5;
  float sw = fract((ang - uTime * uSweep) / 6.28318);
  a += pow(1.0 - sw, 5.0) * 0.34 * step(0.08, r) * step(r, 0.9);
  float amberTick = step(0.9, fract((t - uTime * 0.01) * 12.0)) * step(0.865, r) * step(r, 0.915);
  a += ring(r, uPulseR, 0.025) * uPulseA;
  vec3 col = uTint;
  col = mix(col, vec3(1.0, 0.72, 0.3), clamp(amberTick * 1.2, 0.0, 1.0));
  a += amberTick * 0.6;
  gl_FragColor = vec4(col, clamp(a * uPower, 0.0, 1.0));
}
`;

const CONE_VERT = /* glsl */ `
varying float vY; varying vec3 vW; varying float vA;
void main(){
  vY = position.y / 3.6 + 0.5;
  vA = atan(position.z, position.x);
  vec4 w = modelMatrix * vec4(position, 1.0);
  vW = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}
`;

const CONE_FRAG = /* glsl */ `
uniform float uTime, uPower;
uniform vec3 uTint;
varying float vY; varying vec3 vW; varying float vA;
void main(){
  float up = pow(1.0 - vY, 1.7);
  float streak = pow(sin(vA * 38.0 + uTime * 0.6) * 0.5 + 0.5, 10.0);
  float flow = 0.6 + 0.4 * sin(vY * 24.0 - uTime * 2.0);
  vec3 n = normalize(cross(dFdx(vW), dFdy(vW)));
  float rim = pow(1.0 - abs(dot(n, normalize(cameraPosition - vW))), 1.5);
  float a = up * (0.045 + streak * 0.07 * flow + rim * 0.08 * up);
  gl_FragColor = vec4(uTint, a * uPower);
}
`;

const PART_VERT = /* glsl */ `
attribute vec4 aSeed;
uniform float uTime, uPower, uH, uDpr;
varying float vA; varying float vG; varying float vWarm;
void main(){
  float t = fract(aSeed.z + uTime * (0.05 + aSeed.w * 0.05));
  float y = 0.08 + t * uH;
  float r = aSeed.y * (1.9 - 0.8 * t);
  float ang = aSeed.x * 6.28318 + uTime * 0.25 * (aSeed.w - 0.5);
  vec3 p = vec3(cos(ang) * r, y, sin(ang) * r);
  p.x += sin(uTime * 0.7 + aSeed.z * 20.0) * 0.07;
  vA = sin(3.14159 * t) * uPower;
  vG = floor(aSeed.w * 8.0);
  vWarm = step(0.93, fract(aSeed.z * 7.3));
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = (10.0 + aSeed.w * 12.0) * uDpr * (6.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`;

const PART_FRAG = /* glsl */ `
uniform sampler2D uAtlas;
varying float vA; varying float vG; varying float vWarm;
void main(){
  vec2 c = vec2(mod(vG, 4.0), floor(vG / 4.0));
  vec2 uv = (c + vec2(gl_PointCoord.x, 1.0 - gl_PointCoord.y)) / vec2(4.0, 2.0);
  float g = texture2D(uAtlas, uv).r;
  if (g < 0.05) discard;
  vec3 col = mix(vec3(0.37, 0.89, 1.0), vec3(1.0, 0.72, 0.3), vWarm);
  gl_FragColor = vec4(col, g * vA * 0.85);
}
`;

// ---------------------------------------------------------------- helpers

const ease = (x: number) => {
  const t = Math.min(1, Math.max(0, x));
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
};

type Tween = { from: number; to: number; t0: number; dur: number };

function glyphAtlas(): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 256;
  c.height = 128;
  const g = c.getContext('2d')!;
  g.fillStyle = '#000';
  g.fillRect(0, 0, 256, 128);
  g.fillStyle = '#fff';
  g.font = 'bold 46px monospace';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  '01+·▚░<>'
    .split('')
    .forEach((ch, i) => g.fillText(ch, (i % 4) * 64 + 32, Math.floor(i / 4) * 64 + 34));
  const t = new THREE.CanvasTexture(c);
  t.minFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  return t;
}

function glyphRing(): THREE.CanvasTexture {
  const S = 1024;
  const c = document.createElement('canvas');
  c.width = c.height = S;
  const g = c.getContext('2d')!;
  g.clearRect(0, 0, S, S);
  g.font = '20px monospace';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  const chars = '01<>/\\|+:·▚▞░▒01';
  const rings: [number, number][] = [
    [0.8, 96],
    [0.7, 80],
  ];
  let k = 7;
  for (const [fr, n] of rings) {
    for (let i = 0; i < n; i++) {
      k = (k * 1103515245 + 12345) & 0x7fffffff;
      const ang = (i / n) * Math.PI * 2;
      g.save();
      g.translate(S / 2 + Math.cos(ang) * fr * (S / 2), S / 2 + Math.sin(ang) * fr * (S / 2));
      g.rotate(ang + Math.PI / 2);
      g.fillStyle =
        k % 11 === 0 ? 'rgba(255,179,71,0.95)' : `rgba(95,227,255,${0.45 + (k % 5) * 0.1})`;
      g.fillText(chars[k % chars.length], 0, 0);
      g.restore();
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

type Rig = {
  role: string;
  hingeP: THREE.Vector3;
  hingeA: THREE.Vector3;
  hingeLim: number;
  swingP: THREE.Vector3;
  swingA: THREE.Vector3;
  swingLim: number;
  centre: THREE.Vector3;
  spinA: THREE.Vector3;
  slideD: THREE.Vector3;
  slideLen: number;
  homeM: THREE.Matrix4;
  v: {
    open: number;
    swing: number;
    turn: number;
    slide: number;
    spin: number;
    level: number;
    gl: number;
  };
  moving: boolean;
  glow?: THREE.Mesh;
  halo?: THREE.Sprite;
  edge?: THREE.LineSegments;
  offFace?: THREE.ShaderMaterial;
  offEdge?: THREE.ShaderMaterial;
  faceMat?: THREE.ShaderMaterial;
  edgeMat?: THREE.ShaderMaterial;
};
type Part = {
  id: string;
  mesh: THREE.Mesh;
  home: THREE.Vector3;
  dir: THREE.Vector3;
  mag: number;
  rig: Rig;
  bb: THREE.Box3;
  rank: number;
  parent?: Part;
  homeInv: THREE.Matrix4;
  D: THREE.Matrix4;
  anyMove: boolean;
  mov: boolean;
};

const UPV = new THREE.Vector3(0, 1, 0);
const AX: Record<string, 'x' | 'y' | 'z'> = { x: 'x', y: 'z', z: 'y' }; // blender (z-up) -> glTF (y-up)
const blVec = (a: unknown): THREE.Vector3 | null =>
  Array.isArray(a) && a.length >= 3 ? new THREE.Vector3(+a[0], +a[2], -+a[1]) : null;
let haloTex: THREE.CanvasTexture | null = null;
function haloTexture(): THREE.CanvasTexture {
  if (haloTex) return haloTex;
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d')!;
  const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, 'rgba(255,255,255,1)');
  gr.addColorStop(0.25, 'rgba(255,255,255,0.45)');
  gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr;
  g.fillRect(0, 0, 128, 128);
  haloTex = new THREE.CanvasTexture(c);
  return haloTex;
}

/** Motion rig for one part: explicit glTF extras (jarvis_*) win, else guessed from geometry. */
function buildRig(
  bb: THREE.Box3,
  ctr: THREE.Vector3,
  role: string,
  ex: Record<string, unknown>,
  maxDim: number,
  homeM: THREE.Matrix4
): Rig {
  const c = bb.getCenter(new THREE.Vector3());
  const sz = bb.getSize(new THREE.Vector3());
  const arr = [sz.x, sz.y, sz.z];
  const comp = (v: THREE.Vector3, i: number) => (i === 0 ? v.x : i === 1 ? v.y : v.z);
  const longI = arr.indexOf(Math.max(...arr));
  const shortI = arr.indexOf(Math.min(...arr));
  const unit = (i: number) => new THREE.Vector3().setComponent(i, 1);
  const exAxis = AX[String(ex.jarvis_axis ?? '').toLowerCase()];
  const exAxisV = exAxis ? unit(exAxis === 'x' ? 0 : exAxis === 'y' ? 1 : 2) : null;
  const exPivot = blVec(ex.jarvis_pivot);
  const lim = typeof ex.jarvis_limit === 'number' ? (ex.jarvis_limit as number) : null;

  // spin: shaft axis = thinnest dimension (wheel axle, fan shaft), about the part centre
  const spinA = exAxisV ?? unit(shortI);

  // swing/turn: pivot = the end of the long axis closest to the object centre
  const lo = c.clone().setComponent(longI, comp(bb.min, longI));
  const hi = c.clone().setComponent(longI, comp(bb.max, longI));
  const swingP =
    exPivot && role !== 'hinge' ? exPivot : lo.distanceTo(ctr) <= hi.distanceTo(ctr) ? lo : hi;
  const hv = c.clone().sub(swingP).setY(0);
  const swingA =
    exAxisV && role === 'swing'
      ? exAxisV
      : hv.length() > 0.3 * Math.max(...arr)
        ? hv.cross(UPV).normalize()
        : new THREE.Vector3(0, 0, -1);

  // hinge: flat horizontal part = lid (hinge at rear edge), else vertical door (hinge at inner edge)
  let hingeP = exPivot ?? c.clone();
  let hingeA = exAxisV ?? new THREE.Vector3(0, 1, 0);
  if (!(exPivot && exAxisV)) {
    if (shortI === 1) {
      if (sz.x >= sz.z) {
        hingeP = new THREE.Vector3(c.x, c.y, bb.min.z);
        hingeA = new THREE.Vector3(-1, 0, 0);
      } else {
        hingeP = new THREE.Vector3(bb.min.x, c.y, c.z);
        hingeA = new THREE.Vector3(0, 0, 1);
      }
    } else if (sz.x < sz.z) {
      const zi = Math.abs(bb.min.z - ctr.z) <= Math.abs(bb.max.z - ctr.z) ? bb.min.z : bb.max.z;
      hingeP = new THREE.Vector3(c.x, c.y, zi);
      const free = c.z - zi;
      const out = Math.sign(c.x - ctr.x) || 1;
      hingeA = new THREE.Vector3(0, out * Math.sign(free || 1), 0);
    } else {
      const xi = Math.abs(bb.min.x - ctr.x) <= Math.abs(bb.max.x - ctr.x) ? bb.min.x : bb.max.x;
      hingeP = new THREE.Vector3(xi, c.y, c.z);
      const free = c.x - xi;
      const out = Math.sign(c.z - ctr.z) || 1;
      hingeA = new THREE.Vector3(0, -out * Math.sign(free || 1), 0);
    }
  }
  const sd = longI === 1 ? 0 : longI;
  const slideD = exAxisV ?? unit(sd);
  if (!exAxisV && comp(c, sd) < comp(ctr, sd)) slideD.negate();
  return {
    role,
    hingeP,
    hingeA,
    hingeLim: ((lim ?? 100) * Math.PI) / 180,
    swingP,
    swingA,
    swingLim: ((role === 'swing' && lim ? lim : 60) * Math.PI) / 180,
    centre: c,
    spinA,
    slideD,
    slideLen: Math.max(arr[sd] * 0.7, 0.12 * maxDim, lim && role === 'slide' ? lim : 0),
    homeM: homeM.clone(),
    v: { open: 0, swing: 0, turn: 0, slide: 0, spin: 0, level: 1, gl: 0 },
    moving: false,
  };
}
const _T1 = new THREE.Matrix4();
const _T2 = new THREE.Matrix4();
const _R = new THREE.Matrix4();
/** M = T(p) R(axis, ang) T(-p) * M */
function rotAbout(M: THREE.Matrix4, axis: THREE.Vector3, ang: number, p: THREE.Vector3) {
  if (Math.abs(ang) < 1e-5) return;
  _T1.makeTranslation(p.x, p.y, p.z);
  _T2.makeTranslation(-p.x, -p.y, -p.z);
  _R.makeRotationAxis(axis, ang);
  M.premultiply(_T2).premultiply(_R).premultiply(_T1);
}
type Holo = {
  holder: THREE.Group;
  parts: Part[];
  U: Record<string, { value: unknown }>;
  mats: THREE.ShaderMaterial[];
  tw: Tween;
  tether: THREE.LineSegments;
  dims: THREE.Vector3;
  tris: number;
  radius: number;
  height: number;
  invert: boolean;
  /** merged static faces + edges (1 draw call each) shown while assembled */
  merged: THREE.Object3D[];
  statParts: Part[];
  edgesBuilt: boolean;
  exploded: boolean;
  edgeMat: THREE.ShaderMaterial;
  /** 4D polytope: per-frame rotate-in-4D + project; returns true while animating */
  fourD?: (dt: number, s: HoloState) => boolean;
  /** 0..1 multiplier on the stage disc + glyph ring (sim playback fades them out while the disc morphs into the grid) */
  stageFade?: number;
};

const COMP_RE = /(edge|wire|glow|outline)/;

/** Explode data: split a mesh into connected islands (big ones into spatial chunks) and
 *  give every vertex `aDir` = outward direction from the object centre of its piece
 *  (mesh-local space, length 0.6..1.4). The vertex shader scales it by uExpV. */
function shardify(m: THREE.Mesh, ctr: THREE.Vector3, maxDim: number): void {
  let g = m.geometry as THREE.BufferGeometry;
  if (g.getAttribute('aDir')) return;
  if (g.index) g = g.toNonIndexed();
  const pa = g.getAttribute('position');
  const nTri = Math.floor(pa.count / 3);
  if (nTri < 1) return;
  const key = (i: number) =>
    `${Math.round(pa.getX(i) * 2e3)},${Math.round(pa.getY(i) * 2e3)},${Math.round(pa.getZ(i) * 2e3)}`;
  const ids = new Map<string, number>();
  const vid = new Int32Array(pa.count);
  for (let i = 0; i < pa.count; i++) {
    const k = key(i);
    let id = ids.get(k);
    if (id === undefined) {
      id = ids.size;
      ids.set(k, id);
    }
    vid[i] = id;
  }
  const par = new Int32Array(ids.size).map((_, i) => i);
  const find = (a: number): number => {
    while (par[a] !== a) {
      par[a] = par[par[a]];
      a = par[a];
    }
    return a;
  };
  for (let t = 0; t < nTri; t++) {
    const a = find(vid[t * 3]);
    par[find(vid[t * 3 + 1])] = a;
    par[find(vid[t * 3 + 2])] = a;
  }
  const mat = m.matrix;
  const v = new THREE.Vector3();
  const triC = new Float32Array(nTri * 3);
  const isl = new Int32Array(nTri);
  const bbs = new Map<number, THREE.Box3>();
  const cnt = new Map<number, number>();
  for (let t = 0; t < nTri; t++) {
    let cx = 0,
      cy = 0,
      cz = 0;
    for (let q = 0; q < 3; q++) {
      v.fromBufferAttribute(pa, t * 3 + q).applyMatrix4(mat);
      cx += v.x / 3;
      cy += v.y / 3;
      cz += v.z / 3;
    }
    triC.set([cx, cy, cz], t * 3);
    const id = find(vid[t * 3]);
    isl[t] = id;
    let b = bbs.get(id);
    if (!b) bbs.set(id, (b = new THREE.Box3()));
    b.expandByPoint(v.set(cx, cy, cz));
    cnt.set(id, (cnt.get(id) ?? 0) + 1);
  }
  // big islands -> 3x3x3 spatial chunks so single-piece objects still fly apart
  const chunk = new Map<string, { s: THREE.Vector3; n: number }>();
  const tChunk: string[] = new Array(nTri);
  const sz = new THREE.Vector3();
  for (let t = 0; t < nTri; t++) {
    const id = isl[t];
    const b = bbs.get(id)!;
    b.getSize(sz);
    let k = `${id}`;
    if (Math.max(sz.x, sz.y, sz.z) > 0.45 * maxDim && (cnt.get(id) ?? 0) >= 24) {
      const c = (a: number, lo: number, ext: number) =>
        Math.min(2, Math.floor(((a - lo) / Math.max(ext, 1e-6)) * 3));
      k += `:${c(triC[t * 3], b.min.x, sz.x)}${c(triC[t * 3 + 1], b.min.y, sz.y)}${c(triC[t * 3 + 2], b.min.z, sz.z)}`;
    }
    tChunk[t] = k;
    let ch = chunk.get(k);
    if (!ch) chunk.set(k, (ch = { s: new THREE.Vector3(), n: 0 }));
    ch.s.add(v.set(triC[t * 3], triC[t * 3 + 1], triC[t * 3 + 2]));
    ch.n++;
  }
  const inv = mat.clone().invert();
  const dirs = new Map<string, THREE.Vector3>();
  let ci = 0;
  chunk.forEach((ch, k) => {
    const c = ch.s.clone().divideScalar(ch.n).sub(ctr);
    const dist = c.length();
    const d =
      dist > 1e-3 * maxDim
        ? c.normalize()
        : new THREE.Vector3(Math.cos(ci * 2.4), 0.35, Math.sin(ci * 2.4)).normalize();
    ci++;
    d.multiplyScalar(0.6 + 0.8 * Math.min(1, dist / (0.5 * maxDim)));
    const len = d.length();
    dirs.set(k, d.transformDirection(inv).multiplyScalar(len)); // holder dir -> mesh-local
  });
  const out = new Float32Array(pa.count * 3);
  for (let t = 0; t < nTri; t++) {
    const d = dirs.get(tChunk[t])!;
    for (let q = 0; q < 3; q++) out.set([d.x, d.y, d.z], (t * 3 + q) * 3);
  }
  g.setAttribute('aDir', new THREE.BufferAttribute(out, 3));
  // per-vertex colour (glTF COLOR_0) -> aCol; zeros = no colour (use the hologram tint)
  const ca = g.getAttribute('color');
  const col = new Float32Array(pa.count * 3);
  if (ca) {
    for (let i = 0; i < pa.count; i++) col.set([ca.getX(i), ca.getY(i), ca.getZ(i)], i * 3);
    g.deleteAttribute('color');
  }
  g.setAttribute('aCol', new THREE.BufferAttribute(col, 3));
  if (g !== m.geometry) (m.geometry as THREE.BufferGeometry).dispose();
  m.geometry = g;
}

/** Per-object idle signature: deterministic from the slug, so every hologram moves in its own way. */
const sigCache = new Map<string, ReturnType<typeof idleSigRaw>>();
function idleSig(slug?: string | null) {
  const k = slug ?? 'object';
  let v = sigCache.get(k);
  if (!v) sigCache.set(k, (v = idleSigRaw(k)));
  return v;
}
function idleSigRaw(slug: string) {
  let h = 2166136261;
  for (const ch of slug) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  const r = (k: number) => {
    const x = Math.sin((h >>> 0) * 0.000001 + k * 78.233) * 43758.5453;
    return x - Math.floor(x);
  };
  return {
    vec: new THREE.Vector3(r(1) * 40, (r(2) - 0.5) * 0.5, r(3) * 40),
    bobA: 0.015 + r(4) * 0.06,
    bobF: 0.5 + r(5) * 0.8,
    pitchA: (0.8 + r(6) * 3.2) * (Math.PI / 180),
    pitchF: 0.35 + r(7) * 0.5,
    rollA: (0.6 + r(8) * 2.6) * (Math.PI / 180),
    rollF: 0.3 + r(9) * 0.6,
    breA: r(10) * 0.018,
    breF: 0.6 + r(11) * 0.9,
    ph: r(12) * 6.28,
  };
}

function prettyName(slug?: string | null): string {
  return (slug ?? 'object')
    .replace(/-t\d+-.*$/, '')
    .replace(/^(a|an|the)-/, '')
    .replace(/-/g, ' ')
    .toUpperCase()
    .slice(0, 30);
}

// ---------------------------------------------------------------- component

export default function HoloStage({ state, fetchGlb, fetchShape, fetchSim }: Props) {
  const live = useRef<HoloState | null>(state);
  const fetchRef = useRef(fetchGlb);
  live.current = state;
  fetchRef.current = fetchGlb;
  const fetchShapeRef = useRef(fetchShape);
  fetchShapeRef.current = fetchShape;
  const fetchSimRef = useRef(fetchSim);
  fetchSimRef.current = fetchSim;
  const want = !!state?.open && !!state?.has_glb;
  const [mounted, setMounted] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const tag = useRef<HTMLDivElement>(null);
  const scrim = useRef<HTMLDivElement>(null);
  const cap = useRef<HTMLDivElement>(null);
  const capHeard = useRef<HTMLDivElement>(null);
  const capReply = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (want) {
      setMounted(true);
      return;
    }
    if (!mounted) return;
    const id = setTimeout(() => setMounted(false), 3400);
    return () => clearTimeout(id);
  }, [want, mounted]);

  useEffect(() => {
    if (!mounted || !host.current) return;
    const el = host.current;
    const W = () => window.innerWidth;
    const H = () => window.innerHeight;
    const renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance',
    });
    let dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    renderer.setPixelRatio(dpr);
    renderer.setSize(W(), H());
    renderer.setClearColor(0x000000, 0);
    renderer.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;';
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(32, W() / H(), 0.1, 100);
    const root = new THREE.Group();
    const pivot = new THREE.Group();
    root.add(pivot);
    scene.add(root);

    const shared = {
      uTime: { value: 0 },
      uFlick: { value: 1 },
      uGlitch: { value: 0 },
      uSolid: { value: 0 },
      uScan: { value: 0 },
      uExpV: { value: 0 },
      uIdleA: { value: 0 },
      uIdleP: { value: new THREE.Vector3() },
      uTint: { value: CYAN.clone() },
    };

    // ---- stage: disc, glyph ring, cone, particles, front ring
    const power = { v: 0 };
    const pulse = { r: 0, a: 0 };
    const discU = {
      uTime: shared.uTime,
      uTint: shared.uTint,
      uPower: { value: 0 },
      uSweep: { value: 0.9 },
      uPulseR: { value: 0 },
      uPulseA: { value: 0 },
    };
    const disc = new THREE.Mesh(
      new THREE.PlaneGeometry(4.6, 4.6),
      new THREE.ShaderMaterial({
        uniforms: discU,
        vertexShader: DISC_VERT,
        fragmentShader: DISC_FRAG,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      })
    );
    disc.rotation.x = -Math.PI / 2;
    disc.renderOrder = 0;
    scene.add(disc);

    const ringTex = glyphRing();
    const ringMat = new THREE.MeshBasicMaterial({
      map: ringTex,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      opacity: 0,
    });
    const ringGroup = new THREE.Group();
    ringGroup.rotation.x = -Math.PI / 2;
    ringGroup.position.y = 0.012;
    const ringMesh = new THREE.Mesh(new THREE.PlaneGeometry(4.8, 4.8), ringMat);
    ringMesh.renderOrder = 1;
    ringGroup.add(ringMesh);
    scene.add(ringGroup);

    const coneU = { uTime: shared.uTime, uTint: shared.uTint, uPower: { value: 0 } };
    const cone = new THREE.Mesh(
      new THREE.CylinderGeometry(1.15, 2.05, 3.6, 64, 1, true),
      new THREE.ShaderMaterial({
        uniforms: coneU,
        vertexShader: CONE_VERT,
        fragmentShader: CONE_FRAG,
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending,
      })
    );
    cone.position.y = 1.8;
    cone.renderOrder = 2;
    scene.add(cone);

    const N = 150;
    const seed = new Float32Array(N * 4);
    for (let i = 0; i < N * 4; i++) seed[i] = Math.random();
    const pg = new THREE.BufferGeometry();
    pg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3));
    pg.setAttribute('aSeed', new THREE.BufferAttribute(seed, 4));
    pg.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 1.5, 0), 6);
    const atlas = glyphAtlas();
    const partU = {
      uTime: shared.uTime,
      uPower: { value: 0 },
      uH: { value: 3.4 },
      uDpr: { value: dpr },
      uAtlas: { value: atlas },
    };
    const particles = new THREE.Points(
      pg,
      new THREE.ShaderMaterial({
        uniforms: partU,
        vertexShader: PART_VERT,
        fragmentShader: PART_FRAG,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      })
    );
    particles.frustumCulled = false;
    particles.renderOrder = 6;
    scene.add(particles);

    const frontRingMat = new THREE.MeshBasicMaterial({
      color: 0xbdf3ff,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
    const frontRing = new THREE.Mesh(new THREE.RingGeometry(0.97, 1.0, 96, 1), frontRingMat);
    frontRing.rotation.x = -Math.PI / 2;
    frontRing.renderOrder = 7;
    frontRing.visible = false;
    pivot.parent!.add(frontRing);
    const frontFillMat = new THREE.MeshBasicMaterial({
      color: 0x5fe3ff,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
    const frontFill = new THREE.Mesh(new THREE.CircleGeometry(1, 64), frontFillMat);
    frontFill.rotation.x = -Math.PI / 2;
    frontFill.renderOrder = 7;
    frontFill.visible = false;
    root.add(frontFill);

    // ---- models
    let cur: Holo | null = null;
    const olds: Holo[] = [];
    let loadedStamp = '';
    let loading = false;
    let pendingBuild = true;
    let dead = false;
    let explodeV = 0;
    let rotY = 0;
    let rotX = 0;
    let rotZ = 0;
    let pitchV = 0;
    let yawV = 0;
    let rollV = 0;
    pivot.rotation.order = 'YXZ';
    let zoomV = 1;
    let prevOpen = true;
    let prevCmdSeq = live.current?.cmd_seq ?? 0;
    let glitchT = 0;
    let nextGlitch = 3 + Math.random() * 4;
    let dipT = 0;
    let cmdMix = 0;
    const cmdTint = new THREE.Color();
    let tagTimer = 0;
    let tagText = '';
    let capText = '';
    let needs = true; // on-demand rendering: idle = zero GPU
    let slow = 0;
    let prevSeq = -1;
    let prevPower = 0;
    let builtOnce = false;
    let bootT = 99;
    let bootStep = 99;
    let lastRendered = false;
    const t0 = performance.now();
    const now = () => (performance.now() - t0) / 1000;

    const setTag = (text: string, secs: number) => {
      tagText = text;
      tagTimer = secs;
    };

    const makeModel = (gltf: { scene: THREE.Object3D }, invert: boolean): Holo => {
      const src = gltf.scene;
      src.updateMatrixWorld(true);
      const rootInv = src.matrixWorld.clone().invert();
      const meshes: THREE.Mesh[] = [];
      src.traverse((o) => {
        if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh);
      });
      const holder = new THREE.Group();
      for (const m of meshes) {
        const rel = m.matrixWorld.clone().premultiply(rootInv);
        m.removeFromParent();
        rel.decompose(m.position, m.quaternion, m.scale);
        holder.add(m);
      }
      holder.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(holder);
      const size = box.getSize(new THREE.Vector3());
      const ctr = box.getCenter(new THREE.Vector3());
      // spin axis through the vertex mass centre (not the bbox) so lopsided objects orbit their body
      const mc = new THREE.Vector3();
      const vp = new THREE.Vector3();
      let vn = 0;
      for (const m of meshes) {
        const pa = (m.geometry as THREE.BufferGeometry).getAttribute('position');
        const step = Math.max(1, Math.floor(pa.count / 4000));
        for (let i = 0; i < pa.count; i += step) {
          vp.fromBufferAttribute(pa, i).applyMatrix4(m.matrix);
          mc.add(vp);
          vn++;
        }
      }
      if (vn) {
        mc.divideScalar(vn);
        ctr.x = mc.x;
        ctr.z = mc.z;
      }
      const k = Math.min(
        2.5 / Math.max(size.y, 1e-4),
        3.3 / Math.max(Math.hypot(size.x, size.z), 1e-4)
      );
      const H = size.y * k;
      const bottom = 0.55;
      const wrap = new THREE.Group();
      holder.position.set(-ctr.x, -box.min.y, -ctr.z);
      wrap.add(holder);
      wrap.scale.setScalar(k);
      wrap.position.y = bottom;

      const U: Holo['U'] = {
        ...shared,
        uReveal: { value: 0 },
        uInvert: { value: invert ? 1 : 0 },
        uY0: { value: bottom },
        uY1: { value: bottom + H },
        uLead: { value: 0.2 },
        uExpMag: { value: 0.34 * Math.max(size.x, size.y, size.z) },
        uIdleA: { value: 0.018 * Math.max(size.x, size.y, size.z) },
        uIdleP: { value: idleSig(live.current?.slug).vec },
      };
      const mk = (frag: string) =>
        new THREE.ShaderMaterial({
          uniforms: U,
          vertexShader: MODEL_VERT,
          fragmentShader: frag,
          transparent: true,
          depthWrite: false,
          side: THREE.DoubleSide,
          blending: THREE.AdditiveBlending,
        });
      const faceMat = mk(FACE_FRAG);
      const edgeMat = mk(EDGE_FRAG);
      const metaById = new Map((live.current?.parts ?? []).map((q) => [q.id, q]));
      const maxDim = Math.max(size.x, size.y, size.z);
      const parts: Part[] = [];
      let tris = 0;
      let maxR = 0;
      const tmp = new THREE.Box3();
      const movIds = new Set(live.current?.movable ?? []);
      const names = meshes.map((q) => q.name);
      // Blender-built wire duplicates (<part>_edge_lines...): hidden, edges are drawn here
      const isComp = (n: string) =>
        names.some((b) => b !== n && n.startsWith(b + '_') && COMP_RE.test(n.slice(b.length)));
      const kept = meshes.filter((m) => {
        if (!isComp(m.name)) return true;
        m.removeFromParent();
        (m.geometry as THREE.BufferGeometry).dispose();
        return false;
      });
      kept.forEach((m, i) => {
        shardify(m, ctr, maxDim);
        const g = m.geometry as THREE.BufferGeometry;
        tris += (g.index ? g.index.count : g.getAttribute('position').count) / 3;
        m.material = faceMat;
        m.renderOrder = 3;
        if (!g.boundingBox) g.computeBoundingBox();
        tmp.copy(g.boundingBox!).applyMatrix4(m.matrix);
        const meta = metaById.get(m.name);
        const mov = movIds.has(m.name) || !!meta?.extras?.jarvis_motion;
        const rig = buildRig(
          tmp,
          ctr,
          meta?.role ?? 'generic',
          meta?.extras ?? {},
          maxDim,
          m.matrix
        );
        rig.faceMat = faceMat;
        rig.edgeMat = edgeMat;
        if (rig.role === 'light' && mov) {
          const lc = new THREE.Color(0xffd9a0);
          rig.glow = new THREE.Mesh(
            g,
            new THREE.MeshBasicMaterial({
              color: lc,
              transparent: true,
              opacity: 0,
              depthWrite: false,
              blending: THREE.AdditiveBlending,
              side: THREE.DoubleSide,
            })
          );
          rig.glow.renderOrder = 6;
          rig.glow.frustumCulled = false;
          m.add(rig.glow);
          const halo = new THREE.Sprite(
            new THREE.SpriteMaterial({
              map: haloTexture(),
              color: lc,
              transparent: true,
              opacity: 0,
              depthWrite: false,
              blending: THREE.AdditiveBlending,
            })
          );
          const gc = g.boundingBox!.getCenter(new THREE.Vector3());
          halo.position.copy(gc);
          const sz = g.boundingBox!.getSize(new THREE.Vector3());
          halo.scale.setScalar(Math.max(sz.x, sz.y, sz.z, 0.08 * maxDim) * 3.2);
          halo.renderOrder = 8;
          m.add(halo);
          rig.halo = halo;
          const offU = { ...U, uTint: { value: new THREE.Color(0x0e3340) } };
          const mkOff = (frag: string) =>
            new THREE.ShaderMaterial({
              uniforms: offU,
              vertexShader: MODEL_VERT,
              fragmentShader: frag,
              transparent: true,
              depthWrite: false,
              side: THREE.DoubleSide,
              blending: THREE.AdditiveBlending,
            });
          rig.offFace = mkOff(FACE_FRAG);
          rig.offEdge = mkOff(EDGE_FRAG);
        }
        // offset direction from the object centre (holder space, centre-relative)
        const cc = tmp.getCenter(new THREE.Vector3()).sub(ctr);
        const dist = cc.length();
        const dir =
          dist > 1e-4 * maxDim
            ? cc.clone().normalize()
            : new THREE.Vector3(Math.cos(i * 2.4), 0.6, Math.sin(i * 2.4)).normalize();
        parts.push({
          id: m.name,
          rig,
          bb: tmp.clone(),
          rank: 0,
          homeInv: m.matrix.clone().invert(),
          D: new THREE.Matrix4(),
          anyMove: false,
          mov,
          mesh: m,
          home: m.position.clone(),
          dir,
          mag: 0,
        });
        maxR = Math.max(
          maxR,
          Math.hypot(cc.x, cc.z) + Math.max(tmp.max.x - tmp.min.x, tmp.max.z - tmp.min.z) / 2
        );
      });
      // moving a group (all "arm" pieces, both doors of a side...) pivots around the group as a whole
      const byId = new Map(parts.map((q) => [q.id, q]));
      for (const cap of live.current?.caps ?? []) {
        const members = cap.parts.map((id) => byId.get(id)).filter((q): q is Part => !!q);
        if (members.length < 2 || cap.role === 'light') continue;
        const ub = new THREE.Box3();
        members.forEach((q) => ub.union(q.bb));
        const ur = buildRig(ub, ctr, cap.role, {}, maxDim, new THREE.Matrix4());
        for (const q of members) {
          const r = q.rig;
          r.hingeP = ur.hingeP;
          r.hingeA = ur.hingeA;
          r.swingP = ur.swingP;
          r.swingA = ur.swingA;
          r.centre = ur.centre;
          r.spinA = ur.spinA;
          r.slideD = ur.slideD;
          r.slideLen = ur.slideLen;
        }
      }
      // attached outer parts follow the part they hang from (shade follows arm, hand follows forearm)
      const baseP = new THREE.Vector3(ctr.x, box.min.y, ctr.z);
      for (const q of parts) q.rank = q.bb.getCenter(new THREE.Vector3()).distanceTo(baseP);
      parts.sort((a, b) => a.rank - b.rank);
      const grow = 0.02 * maxDim;
      // a group's rank is its innermost member, so a group never chains onto itself or onto groups above it
      const capRank = new Map<string, number>();
      const capOf = new Map<string, string>();
      for (const cap of live.current?.caps ?? []) {
        const rs = cap.parts.map((id) => byId.get(id)?.rank ?? Infinity);
        capRank.set(cap.id, Math.min(...rs));
        for (const id of cap.parts) capOf.set(id, cap.id);
      }
      const cr = (q: Part) => (capOf.has(q.id) ? capRank.get(capOf.get(q.id)!)! : q.rank);
      parts.forEach((q, i) => {
        const eb = q.bb.clone().expandByScalar(grow);
        for (let j = i - 1; j >= 0; j--) {
          if (cr(parts[j]) < cr(q) && parts[j].bb.intersectsBox(eb)) {
            q.parent = parts[j];
            break;
          }
        }
      });
      // a part hanging from a moving part moves with it (parts sorted inner to outer)
      for (const q of parts) if (q.parent?.mov) q.mov = true;
      const mkEdges = (m: THREE.Mesh) => {
        const src = m.geometry as THREE.BufferGeometry;
        const eg = new THREE.EdgesGeometry(src, 25);
        if (eg.getAttribute('position').count >= 600000) return null;
        const sa = src.getAttribute('aDir');
        if (sa) {
          const sp = src.getAttribute('position');
          const kk = (x: number, y: number, z: number) =>
            `${Math.round(x * 2e3)},${Math.round(y * 2e3)},${Math.round(z * 2e3)}`;
          const mp = new Map<string, number>();
          for (let i = 0; i < sp.count; i++) mp.set(kk(sp.getX(i), sp.getY(i), sp.getZ(i)), i);
          const ep = eg.getAttribute('position');
          const ad = new Float32Array(ep.count * 3);
          // each segment moves rigidly (both ends take the first end's direction): no stretching
          for (let i = 0; i + 1 < ep.count; i += 2) {
            const j = mp.get(kk(ep.getX(i), ep.getY(i), ep.getZ(i)));
            if (j === undefined) continue;
            const d = [sa.getX(j), sa.getY(j), sa.getZ(j)];
            ad.set(d, i * 3);
            ad.set(d, (i + 1) * 3);
          }
          eg.setAttribute('aDir', new THREE.BufferAttribute(ad, 3));
        }
        const sc = src.getAttribute('aCol');
        if (sc) {
          const sp = src.getAttribute('position');
          const kk = (x: number, y: number, z: number) =>
            `${Math.round(x * 2e3)},${Math.round(y * 2e3)},${Math.round(z * 2e3)}`;
          const mp = new Map<string, number>();
          for (let i = 0; i < sp.count; i++) mp.set(kk(sp.getX(i), sp.getY(i), sp.getZ(i)), i);
          const ep = eg.getAttribute('position');
          const ac = new Float32Array(ep.count * 3);
          for (let i = 0; i < ep.count; i++) {
            const j = mp.get(kk(ep.getX(i), ep.getY(i), ep.getZ(i)));
            if (j !== undefined) ac.set([sc.getX(j), sc.getY(j), sc.getZ(j)], i * 3);
          }
          eg.setAttribute('aCol', new THREE.BufferAttribute(ac, 3));
        }
        return eg;
      };
      const flat = (m: THREE.Mesh, lines: boolean) => {
        const g0 = m.geometry as THREE.BufferGeometry;
        const src = lines ? mkEdges(m) : g0.index ? g0.toNonIndexed() : g0.clone();
        if (!src) return null;
        const pa = src.getAttribute('position');
        const da = src.getAttribute('aDir');
        const ca = src.getAttribute('aCol');
        const arr = new Float32Array(pa.count * 3);
        const darr = da ? new Float32Array(pa.count * 3) : null;
        const v = new THREE.Vector3();
        for (let i = 0; i < pa.count; i++) {
          v.fromBufferAttribute(pa, i).applyMatrix4(m.matrix);
          arr.set([v.x, v.y, v.z], i * 3);
          if (darr && da) {
            v.fromBufferAttribute(da, i);
            const l = v.length();
            v.transformDirection(m.matrix).multiplyScalar(l);
            darr.set([v.x, v.y, v.z], i * 3);
          }
        }
        src.dispose();
        const out = new THREE.BufferGeometry();
        out.setAttribute('position', new THREE.BufferAttribute(arr, 3));
        if (darr) out.setAttribute('aDir', new THREE.BufferAttribute(darr, 3));
        out.setAttribute(
          'aCol',
          ca
            ? new THREE.BufferAttribute(new Float32Array(ca.array as ArrayLike<number>), 3)
            : new THREE.BufferAttribute(new Float32Array(pa.count * 3), 3)
        );
        return out;
      };
      const statParts = parts.filter((q) => !q.mov);
      const merged: THREE.Object3D[] = [];
      if (statParts.length) {
        const fg = statParts.map((q) => flat(q.mesh, false)!);
        const fm = new THREE.Mesh(mergeGeometries(fg)!, faceMat);
        fm.renderOrder = 3;
        fm.frustumCulled = false;
        holder.add(fm);
        merged.push(fm);
        const lg = statParts
          .map((q) => flat(q.mesh, true))
          .filter((x): x is THREE.BufferGeometry => !!x);
        if (lg.length) {
          const lm = new THREE.LineSegments(mergeGeometries(lg)!, edgeMat);
          lm.renderOrder = 4;
          lm.frustumCulled = false;
          holder.add(lm);
          merged.push(lm);
        }
        fg.forEach((x) => x.dispose());
        lg.forEach((x) => x.dispose());
        for (const q of statParts) q.mesh.visible = false;
      }
      for (const q of parts) {
        if (!q.mov) continue;
        const eg = mkEdges(q.mesh);
        if (!eg) continue;
        const e = new THREE.LineSegments(eg, edgeMat);
        e.renderOrder = 4;
        e.frustumCulled = false;
        q.mesh.add(e);
        q.rig.edge = e;
      }
      const tg = new THREE.BufferGeometry();
      tg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(parts.length * 6), 3));
      const tether = new THREE.LineSegments(
        tg,
        new THREE.LineBasicMaterial({
          color: 0xffb347,
          transparent: true,
          opacity: 0,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        })
      );
      tether.frustumCulled = false;
      tether.renderOrder = 5;
      holder.add(tether);
      pivot.add(wrap);
      return {
        holder: wrap,
        parts,
        U,
        mats: [faceMat, edgeMat],
        tw: { from: 0, to: 0, t0: 0, dur: 0 },
        tether,
        dims: size.clone(),
        tris,
        radius: Math.max(maxR * k, 0.8),
        height: H,
        invert,
        merged,
        statParts,
        edgesBuilt: false,
        exploded: false,
        edgeMat,
      };
    };

    // ---- 4D polytope: rotate the 4D vertices every frame, perspective-project to 3D
    const make4D = (spec: Shape4D, invert: boolean): Holo => {
      const V = spec.verts;
      const E = spec.edges;
      const bottom = 0.55;
      const H = 2.7;
      const U: Holo['U'] = {
        ...shared,
        uReveal: { value: 0 },
        uInvert: { value: invert ? 1 : 0 },
        uY0: { value: bottom },
        uY1: { value: bottom + H },
        uLead: { value: 0.2 },
        uExpMag: { value: 1.1 },
        uIdleA: { value: 0 },
      };
      const mat = new THREE.ShaderMaterial({
        uniforms: U,
        vertexShader: MODEL_VERT,
        fragmentShader: EDGE4_FRAG,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      const pos = new Float32Array(E.length * 6);
      const dep = new Float32Array(E.length * 2);
      const dir = new Float32Array(E.length * 6);
      const geo = new THREE.BufferGeometry();
      geo.setAttribute(
        'position',
        new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage)
      );
      geo.setAttribute('aW', new THREE.BufferAttribute(dep, 1).setUsage(THREE.DynamicDrawUsage));
      geo.setAttribute('aDir', new THREE.BufferAttribute(dir, 3).setUsage(THREE.DynamicDrawUsage));
      const lines = new THREE.LineSegments(geo, mat);
      lines.frustumCulled = false;
      lines.renderOrder = 4;
      const wrap = new THREE.Group();
      wrap.position.y = bottom;
      wrap.add(lines);
      pivot.add(wrap);
      const rv = V.map((p) => [0, 0, 0, 0]);
      let a1 = 0;
      let a2 = 0;
      let a3 = 0;
      const D = 2.5;
      const SC = 0.95;
      const fourD = (dt: number, st: HoloState): boolean => {
        const on = st.rot4d !== false;
        const sp = (st.speed ?? 1) * (st.dir ?? 1) * 0.55;
        if (on) {
          a1 += dt * sp;
          a2 += dt * sp * 0.71;
          a3 += dt * sp * 0.53;
        }
        const pl = st.plane4d ?? 'all';
        // angle per rotation plane: [xw, yw, zw, xy]
        const A = [0, 0, 0, 0];
        if (pl === 'xw' || pl === 'all') A[0] = a1;
        if (pl === 'yw' || pl === 'all') A[1] = pl === 'all' ? a2 : a1;
        if (pl === 'zw' || pl === 'all') A[2] = pl === 'all' ? a3 : a1;
        if (pl === 'double') {
          A[3] = a1;
          A[2] = a1;
        }
        const rot = (p: number[], i: number, j: number, th: number) => {
          if (!th) return;
          const c = Math.cos(th);
          const s2 = Math.sin(th);
          const x = p[i] * c - p[j] * s2;
          const y = p[i] * s2 + p[j] * c;
          p[i] = x;
          p[j] = y;
        };
        for (let n = 0; n < V.length; n++) {
          const r = rv[n];
          r[0] = V[n][0];
          r[1] = V[n][1];
          r[2] = V[n][2];
          r[3] = V[n][3];
          rot(r, 0, 3, A[0]);
          rot(r, 1, 3, A[1]);
          rot(r, 2, 3, A[2]);
          rot(r, 0, 1, A[3]);
        }
        for (let e = 0; e < E.length; e++) {
          for (let q = 0; q < 2; q++) {
            const r = rv[E[e][q]];
            const k = (D / (D - r[3] * 0.9)) * SC;
            const o = (e * 2 + q) * 3;
            const x = r[0] * k;
            const y = r[1] * k + 1.35;
            const z = r[2] * k;
            pos[o] = x;
            pos[o + 1] = y;
            pos[o + 2] = z;
            dep[e * 2 + q] = r[3];
            const l = Math.hypot(x, y - 1.35, z) || 1;
            dir[o] = x / l;
            dir[o + 1] = (y - 1.35) / l;
            dir[o + 2] = z / l;
          }
        }
        geo.getAttribute('position').needsUpdate = true;
        geo.getAttribute('aW').needsUpdate = true;
        geo.getAttribute('aDir').needsUpdate = true;
        return on;
      };
      fourD(0, {});
      const tg = new THREE.BufferGeometry();
      tg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
      const tether = new THREE.LineSegments(tg, new THREE.LineBasicMaterial({ visible: false }));
      return {
        holder: wrap,
        parts: [],
        U,
        mats: [mat],
        tw: { from: 0, to: 0, t0: 0, dur: 0 },
        tether,
        dims: new THREE.Vector3(3, 3, 3),
        tris: 0,
        radius: 1.9,
        height: H,
        invert,
        merged: [],
        statParts: [],
        edgesBuilt: true,
        exploded: false,
        edgeMat: mat,
        fourD,
      };
    };

    // ---- physics simulation playback: bodies follow the computed frames, frame 0 at state.sim_start
    const makeSim = (spec: SimData): Holo => {
      const bottom = 0.55;
      const H = 2.7;
      const U: Holo['U'] = {
        ...shared,
        uReveal: { value: 1 },
        uInvert: { value: 0 },
        uY0: { value: bottom },
        uY1: { value: bottom + H },
        uLead: { value: 0.2 },
        uExpMag: { value: 1.1 },
        uIdleA: { value: 0 },
      };
      const mats: THREE.ShaderMaterial[] = [];
      const mk = (color: string, alpha: number, wire: boolean) => {
        const m = new THREE.ShaderMaterial({
          uniforms: { uC: { value: new THREE.Color(color) }, uA: { value: alpha } },
          vertexShader:
            'void main(){ gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
          fragmentShader:
            'uniform vec3 uC; uniform float uA; void main(){ gl_FragColor = vec4(uC, uA); }',
          transparent: true,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
          wireframe: wire,
        });
        mats.push(m);
        return m;
      };
      const wrap = new THREE.Group();
      // a floor sits on the stage disc; floorless scenes (orbits, pendulums) hang at the usual projection height
      wrap.position.y = spec.floor !== null ? bottom + 0.2 - spec.floor : bottom + 1.35;
      const nb = spec.bodies.length;
      const nf = spec.frames.length;
      const fr = new Float32Array(nf * nb * 3);
      spec.frames.forEach((row, i) => fr.set(row, i * nb * 3));
      // sim is Z-up, the stage is Y-up: (x, y, z) -> (x, z, -y)
      const put = (o: THREE.Vector3, i: number, b: number) => {
        const k = (i * nb + b) * 3;
        return o.set(fr[k], fr[k + 2], -fr[k + 1]);
      };
      const v = new THREE.Vector3();
      const v2 = new THREE.Vector3();
      // stage grid: starts as the circular stage (a square grid squeezed into the disc, its outer line = the rim)
      // and morphs into the flat square grid before the simulation starts
      const GN = 12;
      const SEG = 24;
      const RC = 2.2;
      const HALF = 1.9;
      const gridN: number[] = [];
      for (let gi = 0; gi <= GN; gi++) {
        const q = -1 + (2 * gi) / GN;
        for (let k = 0; k < SEG; k++) {
          const a = -1 + (2 * k) / SEG;
          const b = -1 + (2 * (k + 1)) / SEG;
          gridN.push(a, q, b, q, q, a, q, b);
        }
      }
      const nv = gridN.length / 2;
      const gpos = new Float32Array(nv * 3);
      const gridY = spec.floor ?? -1.15;
      const setGrid = (e: number) => {
        for (let k = 0; k < nv; k++) {
          const nx = gridN[k * 2];
          const nz = gridN[k * 2 + 1];
          const cx = nx * Math.sqrt(1 - (nz * nz) / 2) * RC;
          const cz = nz * Math.sqrt(1 - (nx * nx) / 2) * RC;
          gpos[k * 3] = cx + (nx * HALF - cx) * e;
          gpos[k * 3 + 1] = gridY;
          gpos[k * 3 + 2] = cz + (nz * HALF - cz) * e;
        }
        gattr.needsUpdate = true;
      };
      const gg = new THREE.BufferGeometry();
      const gattr = new THREE.BufferAttribute(gpos, 3).setUsage(THREE.DynamicDrawUsage);
      gg.setAttribute('position', gattr);
      const gridMat = mk('#5fe3ff', 0.5, false);
      const gridLines = new THREE.LineSegments(gg, gridMat);
      gridLines.frustumCulled = false;
      gridLines.renderOrder = 3;
      wrap.add(gridLines);
      setGrid(0);
      if (spec.box !== null) {
        const bx = new THREE.LineSegments(
          new THREE.EdgesGeometry(new THREE.BoxGeometry(spec.box * 2, spec.box * 2, spec.box * 2)),
          mk('#5fe3ff', 0.35, false)
        );
        bx.renderOrder = 3;
        wrap.add(bx);
      }
      const bodyMesh: THREE.Mesh[] = [];
      const trails: THREE.Line[] = [];
      spec.bodies.forEach((bd, b) => {
        const g = new THREE.IcosahedronGeometry(bd.r, 1);
        const m = new THREE.Mesh(g, mk(bd.color, 0.95, true));
        m.renderOrder = 5;
        const core = new THREE.Mesh(g, mk(bd.color, 0.28, false));
        core.renderOrder = 4;
        m.add(core);
        wrap.add(m);
        bodyMesh.push(m);
        const tp = new Float32Array(nf * 3);
        for (let i = 0; i < nf; i++) put(v, i, b).toArray(tp, i * 3);
        const tgeo = new THREE.BufferGeometry();
        tgeo.setAttribute('position', new THREE.BufferAttribute(tp, 3));
        tgeo.setDrawRange(0, 0);
        const tl = new THREE.Line(
          tgeo,
          mk(bd.color, bd.fixed || bd.trail === false ? 0 : 0.6, false)
        );
        tl.frustumCulled = false;
        tl.renderOrder = 4;
        wrap.add(tl);
        trails.push(tl);
      });

      // ---- teaching labels: appear at their frame (apex when the ball gets there, range at touchdown...)
      const toV = (q: number[]) => new THREE.Vector3(q[0], q[2], -q[1]);
      const textures: THREE.Texture[] = [];
      const spriteMats: THREE.SpriteMaterial[] = [];
      const labelSprite = (text: string, color: string, anchor: string) => {
        const lines = text.split('\n');
        const fs = 40;
        const cv = document.createElement('canvas');
        const ctx = cv.getContext('2d')!;
        const font = `600 ${fs}px ui-monospace, "JetBrains Mono", Menlo, monospace`;
        ctx.font = font;
        const w = Math.ceil(Math.max(...lines.map((l) => ctx.measureText(l).width))) + 36;
        const h = lines.length * (fs + 10) + 22;
        cv.width = w;
        cv.height = h;
        ctx.font = font;
        ctx.fillStyle = 'rgba(2,14,22,0.62)';
        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.roundRect(2, 2, w - 4, h - 4, 10);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.textBaseline = 'top';
        lines.forEach((l, i) => ctx.fillText(l, 18, 12 + i * (fs + 10)));
        const tex = new THREE.CanvasTexture(cv);
        tex.colorSpace = THREE.SRGBColorSpace;
        textures.push(tex);
        const sm = new THREE.SpriteMaterial({
          map: tex,
          transparent: true,
          depthTest: false,
          depthWrite: false,
        });
        spriteMats.push(sm);
        const sp = new THREE.Sprite(sm);
        sp.scale.set(w * 0.0022, h * 0.0022, 1);
        sp.center.set(anchor === 'r' ? 1 : anchor === 'c' ? 0.5 : 0, 0);
        sp.renderOrder = 9;
        return sp;
      };
      const dashed = (
        a: THREE.Vector3,
        b: THREE.Vector3,
        mat: THREE.ShaderMaterial,
        solid: boolean
      ) => {
        const out: number[] = [];
        const len = a.distanceTo(b);
        if (solid || len < 0.2) out.push(a.x, a.y, a.z, b.x, b.y, b.z);
        else {
          const n = Math.max(2, Math.floor(len / 0.14));
          for (let i = 0; i < n; i++) {
            const p0 = a.clone().lerp(b, i / n);
            const p1 = a.clone().lerp(b, (i + 0.55) / n);
            out.push(p0.x, p0.y, p0.z, p1.x, p1.y, p1.z);
          }
        }
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(out), 3));
        const ls = new THREE.LineSegments(g, mat);
        ls.frustumCulled = false;
        ls.renderOrder = 6;
        return ls;
      };
      type MarkObj = { grp: THREE.Group; f: number; tag?: number; sp?: THREE.Sprite };
      const markObjs: MarkObj[] = [];
      (spec.marks ?? []).forEach((mk0) => {
        const grp = new THREE.Group();
        grp.visible = false;
        const col = mk0.c ?? mk0.col ?? '#5fe3ff';
        const anchor = typeof mk0.a === 'string' ? mk0.a : 'l';
        const lm = mk(col, 0.85, false);
        let sp: THREE.Sprite | undefined;
        if (mk0.t === 'pt' && mk0.p) {
          const pos = toV(mk0.p);
          const dot = new THREE.Mesh(new THREE.IcosahedronGeometry(0.045, 0), mk(col, 0.95, true));
          dot.position.copy(pos);
          dot.renderOrder = 7;
          grp.add(dot);
          if (mk0.text) {
            sp = labelSprite(mk0.text, col, anchor);
            sp.position
              .copy(pos)
              .add(
                new THREE.Vector3(
                  anchor === 'r' ? -0.07 : anchor === 'l' ? 0.07 : 0,
                  mk0.up ?? 0.08,
                  0
                )
              );
            grp.add(sp);
          }
        } else if (mk0.t === 'line' && Array.isArray(mk0.a) && mk0.b) {
          const a = toV(mk0.a);
          const b = toV(mk0.b);
          grp.add(dashed(a, b, lm, !mk0.dash));
          if (mk0.arrow) {
            const tip = new THREE.Mesh(new THREE.ConeGeometry(0.035, 0.1, 6), mk(col, 0.95, true));
            tip.position.copy(b);
            tip.quaternion.setFromUnitVectors(
              new THREE.Vector3(0, 1, 0),
              b.clone().sub(a).normalize()
            );
            tip.renderOrder = 7;
            grp.add(tip);
          }
          if (mk0.text) {
            sp = labelSprite(mk0.text, col, 'c');
            sp.position
              .copy(a)
              .lerp(b, 0.5)
              .add(new THREE.Vector3(0, mk0.low ? -0.2 : 0.07, 0));
            grp.add(sp);
          }
        } else if (mk0.t === 'arc' && mk0.c0 && mk0.u && mk0.v) {
          const c0 = toV(mk0.c0);
          const u = toV([mk0.u[0], mk0.u[1], mk0.u[2]])
            .sub(toV([0, 0, 0]))
            .normalize();
          const w2 = toV([mk0.v[0], mk0.v[1], mk0.v[2]])
            .sub(toV([0, 0, 0]))
            .normalize();
          const ang = u.angleTo(w2);
          const r = mk0.r ?? 0.5;
          const pts: number[] = [];
          const axis = new THREE.Vector3().crossVectors(u, w2).normalize();
          for (let i = 0; i <= 24; i++) {
            const q = u
              .clone()
              .applyAxisAngle(axis, (ang * i) / 24)
              .multiplyScalar(r)
              .add(c0);
            if (i > 0 && i < 24) pts.push(q.x, q.y, q.z);
            pts.push(q.x, q.y, q.z);
          }
          const g = new THREE.BufferGeometry();
          g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pts), 3));
          const ls = new THREE.LineSegments(g, lm);
          ls.frustumCulled = false;
          ls.renderOrder = 6;
          grp.add(ls);
          if (mk0.text) {
            sp = labelSprite(mk0.text, col, 'l');
            const mid = u
              .clone()
              .applyAxisAngle(axis, ang / 2)
              .multiplyScalar(r + 0.07)
              .add(c0);
            sp.position.copy(mid);
            grp.add(sp);
          }
        } else if (mk0.t === 'tag' && mk0.text) {
          sp = labelSprite(mk0.text, col, 'c');
          grp.add(sp);
        }
        wrap.add(grp);
        markObjs.push({ grp, f: mk0.f, tag: mk0.t === 'tag' ? mk0.body : undefined, sp });
      });
      const labelMats = new THREE.ShaderMaterial();
      labelMats.dispose = () => {
        textures.forEach((x) => x.dispose());
        spriteMats.forEach((x) => x.dispose());
      };
      mats.push(labelMats);
      pivot.add(wrap);
      const MORPH = spec.morph ?? 1.8;
      let lastE = -1;
      let holoRef: Holo | null = null;
      const fourD = (_dt: number, st: HoloState): boolean => {
        const t = Date.now() / 1000 - (st.sim_start ?? 0);
        // circle -> grid morph runs for MORPH seconds, finishing exactly when frame 0 plays
        const m = Math.max(0, Math.min(1, (t + MORPH) / MORPH));
        const e = m * m * (3 - 2 * m);
        if (Math.abs(e - lastE) > 1e-4) {
          lastE = e;
          setGrid(e);
          gridMat.uniforms.uA.value = 0.22 + 0.3 * (1 - e);
          if (holoRef) holoRef.stageFade = 1 - e;
        }
        const appear = Math.max(0, Math.min(1, (m - 0.7) / 0.3));
        const f = Math.max(0, Math.min(nf - 1, t * spec.fps));
        const i0 = Math.floor(f);
        const i1 = Math.min(nf - 1, i0 + 1);
        const a = f - i0;
        for (let b = 0; b < nb; b++) {
          put(v, i0, b);
          put(v2, i1, b);
          bodyMesh[b].position.copy(v).lerp(v2, a);
          bodyMesh[b].scale.setScalar(Math.max(1e-4, appear));
          trails[b].geometry.setDrawRange(0, t < 0 ? 0 : i0 + 1);
        }
        for (const mo of markObjs) {
          const vis = t >= 0 && f >= mo.f;
          mo.grp.visible = vis;
          if (vis && mo.sp) {
            (mo.sp.material as THREE.SpriteMaterial).opacity = Math.min(1, (f - mo.f + 1) / 8);
            if (mo.tag !== undefined) {
              mo.sp.position
                .copy(bodyMesh[mo.tag].position)
                .add(v.set(0, spec.bodies[mo.tag].r + 0.12, 0));
            }
          }
        }
        return t + MORPH < MORPH + 0.01 || t * spec.fps < nf + 20 || m < 1;
      };
      fourD(0, { sim_start: Date.now() / 1000 + 1e6 });
      const tg = new THREE.BufferGeometry();
      tg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
      const tether = new THREE.LineSegments(tg, new THREE.LineBasicMaterial({ visible: false }));
      const holo: Holo = {
        holder: wrap,
        parts: [],
        U,
        mats,
        tw: { from: 0, to: 0, t0: 0, dur: 0 },
        tether,
        dims: new THREE.Vector3(3, 3, 3),
        tris: 0,
        radius: 1.9,
        height: H,
        invert: false,
        merged: [],
        statParts: [],
        edgesBuilt: true,
        exploded: false,
        edgeMat: mats[0] ?? new THREE.ShaderMaterial(),
        fourD,
        stageFade: 1,
      };
      holoRef = holo;
      return holo;
    };

    const dispose = (h: Holo) => {
      pivot.remove(h.holder);
      h.mats.forEach((m) => m.dispose());
      h.holder.traverse((o) => {
        const g = (o as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
        g?.dispose();
      });
    };

    const tween = (h: Holo, to: number, dur: number) => {
      h.tw = { from: h.U.uReveal.value as number, to, t0: now(), dur };
    };

    const load = () => {
      const s = live.current;
      if (!s || loading) return;
      const isSim = !!s.sim && !!fetchSimRef.current;
      const is4 = !isSim && !!s.shape4d && !!fetchShapeRef.current;
      const stamp = isSim
        ? `${s.slug}:sim:${s.sim_mtime}`
        : is4
          ? `${s.slug}:4d:${s.shape4d_mtime}`
          : `${s.slug}:${s.glb_mtime}`;
      if (stamp === loadedStamp || !s.has_glb) return;
      loading = true;
      if (isSim) {
        void fetchSimRef.current!().then((spec) => {
          loading = false;
          if (dead || !spec?.frames?.length) return;
          loadedStamp = stamp;
          const hadModel = !!cur;
          const fresh = makeSim(spec);
          if (cur) {
            const o = cur;
            o.U.uInvert.value = 1;
            tween(o, 1, 0.6);
            o.invert = true;
            olds.push(o);
          }
          cur = fresh;
          needs = true;
          pendingBuild = !hadModel;
          setTag(
            `${(spec.name ?? 'simulation').toUpperCase()} · SIM${spec.rate && Math.abs(spec.rate - 1) > 0.02 ? ' x' + spec.rate : ''}`,
            6
          );
        });
        return;
      }
      if (is4) {
        void fetchShapeRef.current!().then((spec) => {
          loading = false;
          if (dead || !spec?.verts?.length) return;
          loadedStamp = stamp;
          const hadModel = !!cur;
          const fresh = make4D(spec, false);
          if (cur) {
            const o = cur;
            o.U.uInvert.value = 1;
            tween(o, 1, 1.5);
            o.invert = true;
            olds.push(o);
          }
          cur = fresh;
          needs = true;
          if (hadModel && (live.current?.open ?? true)) {
            tween(fresh, 1, 1.5);
            pendingBuild = false;
          } else {
            pendingBuild = true;
          }
          if (!hadModel) setTag((spec.name ?? prettyName(s.slug)).toUpperCase() + ' · 4D', 6);
        });
        return;
      }
      void fetchRef.current().then((buf) => {
        loading = false;
        if (dead || !buf) return;
        loadedStamp = stamp;
        new GLTFLoader().parse(buf, '', (gltf) => {
          if (dead) {
            return;
          }
          const hadModel = !!cur;
          const fresh = makeModel(gltf, false);
          if (cur) {
            const o = cur;
            o.U.uInvert.value = 1;
            tween(o, 1, 1.5);
            o.invert = true;
            olds.push(o);
          }
          cur = fresh;
          needs = true;
          if (hadModel && (live.current?.open ?? true)) {
            tween(fresh, 1, 1.5);
            pendingBuild = false;
            setTag(`UPDATED · V${live.current?.version ?? ''}`, 2.4);
          } else {
            pendingBuild = true;
          }
          if (!hadModel) setTag(prettyName(s.slug) + (s.version ? ` · V${s.version}` : ''), 6);
        });
      });
    };

    const onResize = () => {
      renderer.setSize(W(), H());
      camera.aspect = W() / H();
      camera.updateProjectionMatrix();
      needs = true;
    };
    window.addEventListener('resize', onResize);

    let raf = 0;
    let last = now();
    const gcol = new THREE.Color();
    const MM = new THREE.Matrix4();
    const applyRig = (p: Part, fx: Fx | undefined, dt: number, t: number): boolean => {
      const r = p.rig;
      const v = r.v;
      const k = Math.min(1, dt * 4.5);
      const wave = fx?.wave ? Math.sin(t * 5) * 0.45 : 0;
      v.open += ((fx?.open ?? 0) - v.open) * k;
      v.swing += ((fx?.swing ?? 0) - v.swing) * k;
      v.turn += ((fx?.turn ?? 0) - v.turn) * k;
      v.slide += ((fx?.slide ?? 0) - v.slide) * k;
      v.spin += (fx?.spin ?? 0) * dt * 3;
      const sw = v.swing + wave;
      const live2 =
        Math.abs(v.open) + Math.abs(sw) + Math.abs(v.turn) + Math.abs(v.slide) > 1e-3 ||
        (fx?.spin ?? 0) !== 0;
      const par = p.parent;
      const pm = !!par && par.anyMove;
      let busy = live2 || r.moving || pm || !!fx?.wave;
      if (live2 || r.moving || pm) {
        MM.copy(r.homeM);
        rotAbout(MM, r.spinA, v.spin, r.centre);
        rotAbout(MM, r.hingeA, v.open * r.hingeLim, r.hingeP);
        rotAbout(MM, r.swingA, sw * r.swingLim, r.swingP);
        rotAbout(MM, UPV, v.turn * 1.2, r.swingP);
        if (v.slide > 1e-4) {
          _T1.makeTranslation(
            r.slideD.x * v.slide * r.slideLen,
            r.slideD.y * v.slide * r.slideLen,
            r.slideD.z * v.slide * r.slideLen
          );
          MM.premultiply(_T1);
        }
        if (pm && par) MM.premultiply(par.D);
        p.D.copy(MM).multiply(p.homeInv);
        MM.decompose(p.mesh.position, p.mesh.quaternion, p.mesh.scale);
        p.anyMove = live2 || pm;
        r.moving = p.anyMove;
        if (!p.anyMove) {
          p.mesh.position.copy(p.home);
          p.mesh.quaternion.setFromRotationMatrix(r.homeM.clone().setPosition(0, 0, 0));
        }
      } else {
        p.anyMove = false;
        p.mesh.position.copy(p.home);
      }
      if (r.glow && r.halo) {
        const explicit = fx?.on !== undefined;
        const on = fx?.on !== false;
        const lvl = on ? (fx?.level ?? 1) : 0;
        v.level += (lvl - v.level) * Math.min(1, dt * 6);
        gcol.set(fx?.color ?? '#ffd9a0');
        const gm = r.glow.material as THREE.MeshBasicMaterial;
        gm.color.copy(gcol);
        gm.opacity = v.level * (explicit ? 0.55 : 0.28);
        const hm = r.halo.material as THREE.SpriteMaterial;
        hm.color.copy(gcol);
        hm.opacity = v.level * (explicit ? 0.9 : 0.4);
        const off = v.level < 0.12;
        const wantF = off ? r.offFace : r.faceMat;
        const wantE = off ? r.offEdge : r.edgeMat;
        if (wantF && p.mesh.material !== wantF) p.mesh.material = wantF;
        if (r.edge && wantE && r.edge.material !== wantE) r.edge.material = wantE;
        if (Math.abs(lvl - v.level) > 0.004) busy = true;
      }
      return busy;
    };

    const frame = () => {
      raf = requestAnimationFrame(frame);
      const t = now();
      const dt = Math.min(0.1, t - last);
      last = t;
      const s = live.current ?? {};
      const open = !!s.open && !!s.has_glb;
      shared.uTime.value = t;
      load();
      let busy = false;
      if (s.seq !== prevSeq) {
        prevSeq = s.seq ?? 0;
        busy = true; // any new state (fx colour/target, wire, zoom...) gets at least one frame
      }

      // open/close transitions
      if (open !== prevOpen) {
        prevOpen = open;
        if (open) {
          pendingBuild = true;
          bootT = 0;
          bootStep = 0;
        } else if (cur) {
          tween(cur, 0, 1.7);
          setTag('DE-REZ', 1.6);
        }
      }
      // cinematic power-up: staged tags + pulse rings while the camera dollies in
      if (open && bootT < 4) {
        bootT += dt;
        busy = true;
        const steps: [number, string][] = [
          [0.05, 'PROJECTOR ONLINE'],
          [0.9, 'CALIBRATING EMITTERS'],
          [1.8, 'LOCKING GEOMETRY'],
        ];
        while (bootStep < steps.length && bootT >= steps[bootStep][0]) {
          setTag(steps[bootStep][1], 1.0);
          pulse.r = 0.05;
          pulse.a = 1;
          bootStep++;
        }
      }
      // power (stage) fades in first, out last
      const rev = cur ? (cur.U.uReveal.value as number) : 0;
      if (open) power.v = Math.min(1, power.v + dt / 0.9);
      else if (rev <= 0.02) power.v = Math.max(0, power.v - dt / 0.7);
      if (power.v !== prevPower) {
        prevPower = power.v;
        busy = true;
      }
      if (cur && !builtOnce && rev >= 0.999) {
        builtOnce = true; // lock-on pulse when the build-up completes
        pulse.r = 0.15;
        pulse.a = 1;
      }
      if (open && pendingBuild && cur && power.v > 0.45) {
        pendingBuild = false;
        tween(cur, 1, 2.9);
      }

      // command echo
      const cs = s.cmd_seq ?? 0;
      if (cs !== prevCmdSeq) {
        prevCmdSeq = cs;
        const c = s.cmd ?? '';
        if (c === 'REBUILD' && cur) {
          cur.U.uReveal.value = 0;
          tween(cur, 1, 2.9);
        }
        if (c !== 'DEREZ' && c !== 'PROJECT') {
          cmdTint.copy(CMD_TINT[c] ?? ICE);
          cmdMix = 1;
          pulse.r = 0.15;
          pulse.a = 1;
          setTag(
            c === 'SCAN' && cur
              ? `SCAN · ${cur.dims.x.toFixed(2)} × ${cur.dims.y.toFixed(2)} × ${cur.dims.z.toFixed(2)} · ${Math.round(cur.tris).toLocaleString()} TRIS`
              : c,
            c === 'SCAN' ? 4.5 : 1.6
          );
        }
        if (c === 'REVERSE' || c === 'REBUILD') glitchT = 0.35;
      }
      if (cmdMix > 0) cmdMix = Math.max(0, cmdMix - dt / (s.rotate === false ? 2.2 : 1.1));
      if (s.rotate === false && cmdMix < 0.45) {
        // held: keep a faint amber cast so "stopped" reads at a glance
        cmdTint.copy(AMBER);
        cmdMix = 0.45;
      }
      (shared.uTint.value as THREE.Color).copy(CYAN).lerp(cmdTint, cmdMix);
      if (pulse.a > 0) {
        pulse.r += dt * 1.1;
        pulse.a = Math.max(0, pulse.a - dt * 1.15);
      }

      // flicker + glitch
      let flick = 0.95 + 0.05 * Math.sin(t * 53) * Math.sin(t * 3.7);
      if (power.v < 0.98) flick *= 0.72 + 0.28 * Math.abs(Math.sin(t * 31)); // projector warming up
      if (s.rotate !== false) nextGlitch -= dt; // idle = no glitch frames
      if (nextGlitch <= 0) {
        nextGlitch = 4 + Math.random() * 6;
        glitchT = 0.18;
        dipT = 0.09;
      }
      if (dipT > 0) {
        dipT -= dt;
        flick *= 0.55;
      }
      glitchT = Math.max(0, glitchT - dt);
      shared.uFlick.value = flick;
      shared.uGlitch.value = glitchT > 0 ? 1 : 0;
      const wireT = s.wire === false ? 1 : 0;
      shared.uSolid.value += (wireT - (shared.uSolid.value as number)) * Math.min(1, dt * 4);
      shared.uScan.value +=
        ((s.scan ? 1 : 0) - (shared.uScan.value as number)) * Math.min(1, dt * 5);
      discU.uSweep.value = s.scan ? 3.2 : 0.9;

      // spin / zoom / explode
      if (s.rotate !== false) {
        const da = dt * 0.55 * (s.speed ?? 1) * (s.dir ?? 1);
        if (s.spin_axis === 'x') rotX += da;
        else if (s.spin_axis === 'z') rotZ += da;
        else rotY += da;
      }
      const D2R = Math.PI / 180;
      const kr = Math.min(1, dt * 5);
      pitchV += ((s.pitch ?? 0) * D2R - pitchV) * kr;
      yawV += ((s.yaw ?? 0) * D2R - yawV) * kr;
      rollV += ((s.roll ?? 0) * D2R - rollV) * kr;
      pivot.rotation.set(pitchV + rotX, rotY + yawV, rollV + rotZ, 'YXZ');
      zoomV += ((s.zoom ?? 1) - zoomV) * Math.min(1, dt * 4);
      explodeV += ((s.explode ? 1 : 0) - explodeV) * Math.min(1, dt * 3.2);
      shared.uExpV.value = explodeV;
      if (cur?.fourD && cur.fourD(dt, s)) busy = true;
      if (cur) {
        const gv = explodeV < 0.02;
        for (const q of cur.parts) {
          if (q.rig.glow) q.rig.glow.visible = gv;
          if (q.rig.halo) q.rig.halo.visible = gv;
        }
      }
      const sg = idleSig(s.slug);
      root.position.y = Math.sin(t * sg.bobF + sg.ph) * sg.bobA;
      pivot.rotation.x += Math.sin(t * sg.pitchF + sg.ph * 2) * sg.pitchA;
      pivot.rotation.z += Math.sin(t * sg.rollF + sg.ph * 3) * sg.rollA;
      pivot.scale.setScalar(1 + Math.sin(t * sg.breF + sg.ph) * sg.breA);
      if (open) busy = true; // idle motion keeps drawing
      if (
        s.rotate !== false ||
        s.scan ||
        cmdMix > (s.rotate === false ? 0.4501 : 0) ||
        pulse.a > 0 ||
        glitchT > 0 ||
        dipT > 0 ||
        Math.abs((s.pitch ?? 0) * (Math.PI / 180) - pitchV) > 5e-4 ||
        Math.abs((s.yaw ?? 0) * (Math.PI / 180) - yawV) > 5e-4 ||
        Math.abs((s.roll ?? 0) * (Math.PI / 180) - rollV) > 5e-4 ||
        Math.abs((s.zoom ?? 1) - zoomV) > 5e-4 ||
        Math.abs((s.explode ? 1 : 0) - explodeV) > 5e-4 ||
        Math.abs((s.wire === false ? 1 : 0) - (shared.uSolid.value as number)) > 5e-4 ||
        Math.abs((s.scan ? 1 : 0) - (shared.uScan.value as number)) > 5e-4
      )
        busy = true;

      const H0 = cur?.height ?? 2.2;
      const ty = 0.55 + H0 * (0.42 + 0.12 * explodeV);
      const dist = ((8.6 + H0 * 0.55) * (1 + 0.42 * explodeV)) / zoomV;
      const bk = 1 - ease(Math.min(1, bootT / 3.4)); // 1 → 0 over the power-up
      camera.position.set(
        Math.sin(t * 0.17) * 0.35 + bk * 2.6,
        ty + 1.45 + bk * 1.2,
        dist * (1 + 0.55 * bk)
      );
      camera.lookAt(0, ty - 0.12, 0);

      // models
      const advance = (h: Holo) => {
        const tw = h.tw;
        if (tw.dur > 0)
          h.U.uReveal.value = tw.from + (tw.to - tw.from) * ease((t - tw.t0) / tw.dur);
      };
      if (cur) {
        advance(cur);
        if (cur.tw.dur > 0 && t - cur.tw.t0 < cur.tw.dur + 0.05) busy = true;
        const e = explodeV;
        const fxAll = s.fx ?? {};
        // assembled = merged statics (few draw calls); exploded = every part its own mesh
        const ex = false; // explode is now a vertex-shader burst from the object centre (uExpV)
        if (ex !== cur.exploded) {
          cur.exploded = ex;
          cur.merged.forEach((o) => (o.visible = !ex));
          cur.statParts.forEach((q) => (q.mesh.visible = ex));
          if (ex && !cur.edgesBuilt) {
            cur.edgesBuilt = true;
            for (const q of cur.statParts) {
              const eg = new THREE.EdgesGeometry(q.mesh.geometry as THREE.BufferGeometry, 25);
              if (eg.getAttribute('position').count >= 600000) continue;
              const le = new THREE.LineSegments(eg, cur.edgeMat);
              le.renderOrder = 4;
              le.frustumCulled = false;
              q.mesh.add(le);
            }
          }
          busy = true;
        }
        for (const p of cur.parts) {
          if (!p.mov) {
            if (ex) p.mesh.position.copy(p.home).addScaledVector(p.dir, p.mag * e);
            continue;
          }
          if (applyRig(p, fxAll[p.id], dt, t)) busy = true;
          p.mesh.position.addScaledVector(p.dir, p.mag * e);
        }
        const pos = cur.tether.geometry.getAttribute('position') as THREE.BufferAttribute;
        if (ex || (cur.tether.material as THREE.LineBasicMaterial).opacity > 0.01) {
          cur.parts.forEach((p, i) => {
            pos.setXYZ(i * 2, p.home.x, p.home.y, p.home.z);
            pos.setXYZ(i * 2 + 1, p.mesh.position.x, p.mesh.position.y, p.mesh.position.z);
          });
          pos.needsUpdate = true;
        }
        (cur.tether.material as THREE.LineBasicMaterial).opacity =
          Math.min(1, e * 2) * 0.55 * ease(cur.U.uReveal.value as number);
      }
      for (let i = olds.length - 1; i >= 0; i--) {
        const o = olds[i];
        advance(o);
        busy = true;
        if ((o.U.uReveal.value as number) >= 0.999) {
          dispose(o);
          olds.splice(i, 1);
        }
      }

      // front ring tracks the scan front of the newest model
      const showFront = !!cur && rev > 0.005 && rev < 0.995;
      frontRing.visible = frontFill.visible = showFront;
      if (showFront && cur) {
        const f = Math.min(1, Math.max(0, rev * 1.32 - 0.16));
        const y = cur.U.uY0.value as number;
        const yy = y + cur.height * f;
        frontRing.position.set(0, yy, 0);
        frontFill.position.set(0, yy, 0);
        const rr = cur.radius * 1.12;
        frontRing.scale.setScalar(rr);
        frontFill.scale.setScalar(rr);
        frontRingMat.opacity = 0.85 * flick;
        frontFillMat.opacity = 0.07;
      }

      // stage uniforms
      const pw = power.v;
      const sf = cur?.stageFade ?? 1;
      discU.uPower.value = pw * flick * sf;
      discU.uPulseR.value = pulse.r;
      discU.uPulseA.value = pulse.a * 0.8;
      coneU.uPower.value = pw * flick;
      partU.uPower.value = pw * (0.5 + 0.5 * Math.min(1, rev * 3));
      partU.uH.value = Math.max(2.4, H0 + 1.4);
      ringMat.opacity = pw * 0.7 * flick * sf;
      ringGroup.rotation.z = -t * 0.06;
      disc.rotation.z = 0;

      // tag + scrim
      if (tag.current) {
        tagTimer = Math.max(0, tagTimer - dt);
        if (tag.current.textContent !== tagText)
          tag.current.textContent = tagText ? `▚ ${tagText}` : '';
        tag.current.style.opacity = String(Math.min(1, tagTimer / 0.6) * (open ? 1 : 0.8));
      }
      if (scrim.current) scrim.current.style.opacity = String(Math.min(1, pw * 1.4));

      // caption: last command heard + reply, fades after ~7 s (voice-only UI)
      if (cap.current) {
        const age = s.say_ts ? Date.now() / 1000 - s.say_ts : 99;
        const txt = (s.heard ?? '') + '\u0001' + (s.reply ?? '');
        if (capText !== txt) {
          capText = txt;
          const [hd, rp] = txt.split('\u0001');
          capHeard.current!.textContent = hd ? `\u203a ${hd.toLowerCase()}` : '';
          capReply.current!.textContent = rp ?? '';
        }
        cap.current.style.opacity = String(
          age < 7 && s.heard ? Math.min(1, (7 - age) / 1.5) * 0.9 : 0
        );
      }

      if (busy || needs) {
        needs = false;
        renderer.render(scene, camera);
        // dynamic resolution: step down when continuous frames run slower than ~26 ms
        if (lastRendered && busy) {
          slow = dt * 1000 > 26 ? slow + 1 : Math.max(0, slow - 1);
          if (slow > 20 && dpr > 0.75) {
            dpr = Math.max(0.75, dpr - 0.25);
            renderer.setPixelRatio(dpr);
            renderer.setSize(W(), H());
            partU.uDpr.value = dpr;
            slow = 0;
          }
        }
        lastRendered = true;
        (window as unknown as { __holo?: unknown }).__holo = {
          calls: renderer.info.render.calls,
          tris: renderer.info.render.triangles,
          dpr,
          frames: ((window as unknown as { __holo?: { frames: number } }).__holo?.frames ?? 0) + 1,
        };
      } else {
        lastRendered = false;
      }
    };
    raf = requestAnimationFrame(frame);

    return () => {
      dead = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      if (cur) dispose(cur);
      olds.forEach(dispose);
      [disc, ringMesh, cone, particles, frontRing, frontFill].forEach((m) => {
        m.geometry.dispose();
        (m.material as THREE.Material).dispose();
      });
      atlas.dispose();
      ringTex.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [mounted]);

  if (!mounted) return null;
  return (
    <div aria-hidden style={{ position: 'fixed', inset: 0, zIndex: 60, pointerEvents: 'none' }}>
      <div
        ref={scrim}
        style={{
          position: 'absolute',
          inset: 0,
          opacity: 0,
          background:
            'radial-gradient(ellipse 70% 62% at 50% 52%, rgba(1,8,14,0.78) 0%, rgba(1,6,11,0.93) 62%, rgba(0,3,7,0.97) 100%)',
        }}
      />
      <div
        ref={host}
        style={{
          position: 'absolute',
          inset: 0,
          filter:
            'drop-shadow(0 0 5px rgba(95,227,255,0.55)) drop-shadow(0 0 20px rgba(31,168,214,0.4))',
        }}
      />
      <div
        ref={tag}
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: '9%',
          textAlign: 'center',
          opacity: 0,
          fontFamily: 'ui-monospace, "JetBrains Mono", monospace',
          fontSize: 11,
          letterSpacing: '0.28em',
          color: '#5fe3ff',
          textShadow: '0 0 8px rgba(95,227,255,0.7)',
        }}
      />
      <div
        ref={cap}
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: '14%',
          textAlign: 'center',
          opacity: 0,
          fontFamily: 'ui-monospace, "JetBrains Mono", monospace',
          pointerEvents: 'none',
        }}
      >
        <div
          ref={capHeard}
          style={{ fontSize: 10, letterSpacing: '0.2em', color: '#ffb347', opacity: 0.85 }}
        />
        <div
          ref={capReply}
          style={{
            marginTop: 4,
            fontSize: 12,
            letterSpacing: '0.12em',
            color: '#d8f6ff',
            textShadow: '0 0 8px rgba(95,227,255,0.6)',
          }}
        />
      </div>
    </div>
  );
}
