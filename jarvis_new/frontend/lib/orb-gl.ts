import * as THREE from 'three';

/**
 * Layered holographic orb (replaces the ASCII reactor field).
 *
 * Geometry is authored in Blender (public/orb/orb.json: 14 concentric shells of
 * line work, ~99k segments). Every segment is one instanced screen-space
 * ribbon drawn additively with no depth test or depth write, so shells can
 * never z-clip or z-fight. Each shell spins about its own axis at its own
 * speed (integer revolutions per 6 s loop, scaled by state), driven in the
 * vertex shader from 14 angle uniforms: the CPU only integrates 14 floats per
 * frame. Colour is derived in the fragment stage from one state tint, so a
 * state change, mute or warning is a uniform write, never a rebuild.
 */

export const ORB_URL = '/orb/orb.json';
const NL = 14;
/** Outer radius of the authored orb (largest shell 3.65 + stroke). */
const ORB_R = 4.05;
/** Seconds per authored loop at speed 1; BASE slows idle to a calm drift. */
const LOOP_S = 6;
const BASE = 0.3;
const TAU = Math.PI * 2;

/** Per-shell hue offset around the state tint (-1..1, x HUE_SPREAD degrees). */
const HUE_OFFSET = [0, 0.1, -0.3, 0.05, 0.2, -0.6, -0.8, 0.7, -0.45, 1.0, -0.1, 0.45, 0.85, -0.7];
/** Per-shell push toward white-hot (core shells). */
const HOT = [0.7, 0.45, 0.1, 0.1, 0.35, 0.15, 0, 0, 0, 0, 0, 0, 0, 0];
const HUE_SPREAD = 52;
const AMBER: [number, number, number] = [1, 0.6, 0.12];

export type OrbData = {
  loop: number;
  fps: number;
  layers: {
    name: string;
    radius: number;
    axis: [number, number, number];
    revs: number;
    kind: string;
    strokes: { w: number; c: boolean; p: number[] }[];
  }[];
};

let dataPromise: Promise<OrbData> | null = null;
export function loadOrbData(): Promise<OrbData> {
  dataPromise ??= fetch(ORB_URL).then((r) => {
    if (!r.ok) throw new Error(`orb data ${r.status}`);
    return r.json() as Promise<OrbData>;
  });
  return dataPromise;
}

export function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '');
  if (h.length === 3) {
    h = h
      .split('')
      .map((c) => c + c)
      .join('');
  }
  const n = Number.parseInt(h.slice(0, 6), 16) || 0;
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

const hash = (n: number) => {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
};

const VERT = /* glsl */ `
#define NL ${NL}
// per-layer projection*view*rotation*scale, built on the CPU (14 mat4 / frame)
uniform mat4 uMVP[NL];
uniform float uScale[NL];
uniform float uReveal[NL];
uniform float uOff[NL];
uniform float uHot[NL];
uniform vec2 uRes;
uniform float uWidth;
uniform float uCamZ;
uniform vec3 uTint;
uniform vec3 uAmber;
uniform float uSpread;
uniform float uWarn;
uniform float uMute;
uniform float uBoost;
uniform vec2 uShock;
uniform float uTime;
uniform float uGain;
attribute vec2 aCorner;
attribute vec3 iA;
attribute vec3 iB;
attribute vec4 iM; // layer, stroke hash, width factor, along-stroke 0..1
varying float vEdge;
varying vec4 vCol; // rgb already scaled by every per-stroke factor; a = alpha scale
vec3 hueRot(vec3 c, float a) {
  const vec3 k = vec3(0.57735);
  float ca = cos(a);
  float sa = sin(a);
  return c * ca + cross(k, c) * sa + k * dot(k, c) * (1.0 - ca);
}
void main() {
  int li = int(iM.x + 0.5);
  mat4 m = uMVP[li];
  vec4 c0 = m * vec4(iA, 1.0);
  vec4 c1 = m * vec4(iB, 1.0);
  vec2 s0 = c0.xy / c0.w * uRes * 0.5;
  vec2 s1 = c1.xy / c1.w * uRes * 0.5;
  vec2 d = s1 - s0;
  float len = length(d);
  vec2 dir = len > 1e-4 ? d / len : vec2(1.0, 0.0);
  vec2 nrm = vec2(-dir.y, dir.x);
  vec4 c = mix(c0, c1, aCorner.x);
  vec2 sp = mix(s0, s1, aCorner.x);
  float wpx = uWidth * iM.z;
  sp += dir * (aCorner.x * 2.0 - 1.0) * wpx * 0.5;
  sp += nrm * aCorner.y * wpx;
  gl_Position = vec4(sp / (uRes * 0.5) * c.w, c.z, c.w);
  vEdge = aCorner.y;

  // ---- everything below is constant along a stroke: shade per vertex, not per pixel
  float hsh = iM.y;
  float off = uOff[li];
  float hot = uHot[li];
  vec3 col = max(hueRot(uTint, (off + (hsh - 0.5) * 0.35) * uSpread), vec3(0.0));
  float amberAmt = smoothstep(hsh, hsh + 0.12, uWarn * 1.05);
  col = mix(col, uAmber, amberAmt * 0.92);
  col = mix(col, vec3(1.0), clamp(hot, 0.0, 1.0) * 0.4);
  float pulse = 0.78 + 0.22 * sin(uTime * (0.9 + hsh * 1.8) + iM.w * 12.566 + hsh * 31.0);
  float inten = pulse * (0.55 + 0.6 * hsh);
  float depth = clamp(0.5 + (uCamZ - c.w) / ${(2 * ORB_R).toFixed(2)}, 0.0, 1.0);
  inten *= mix(0.55, 1.0, depth);
  float rad = mix(length(iA), length(iB), aCorner.x) * uScale[li] / ${ORB_R.toFixed(2)};
  float dr = (rad - uShock.x) / 0.13;
  float shock = uShock.y * exp(-dr * dr);
  inten *= 1.0 + uBoost + shock * 2.2;
  inten *= 1.0 + uWarn * 0.3 * (0.5 + 0.5 * sin(uTime * 3.4));
  inten *= uReveal[li] * uGain * (1.0 - 0.5 * uMute);
  col = mix(col, vec3(dot(col, vec3(0.333))) * vec3(0.85, 0.88, 1.0), uMute);
  col = mix(col, vec3(1.0), clamp(shock * 0.5, 0.0, 0.6));
  vCol = vec4(col * inten, 0.0);
}
`;

const FRAG = /* glsl */ `
varying float vEdge;
varying vec4 vCol;
void main() {
  float p = 1.0 - vEdge * vEdge;
  float prof = 1.2 * p * p * p;
  vec3 rgb = vCol.rgb * prof;
  gl_FragColor = vec4(rgb, min(1.0, max(rgb.r, max(rgb.g, rgb.b))));
}
`;

const CORE_VERT = /* glsl */ `
varying vec2 vUv;
void main() { vUv = position.xy; gl_Position = vec4(position.xy * vec2(1.0), 0.0, 1.0); }
`;

export type OrbGLOptions = {
  canvas: HTMLCanvasElement;
  data: OrbData;
};

/** State-driven inputs; all plain values, cheap to set every frame. */
export type OrbInputs = {
  color: string;
  muted: boolean;
  speed: number; // state speed multiplier (idle 1)
  boost: number; // 0..~1.5 energy flare (talk / energise / shock tail)
  warn: number; // 0..1 amber warning level
  energy: number; // 0..1 orb-energy dial (lib/orb-motion)
  reduced: boolean;
};

export class OrbGL {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(22, 1, 1, 100);
  private mat: THREE.ShaderMaterial;
  private core: THREE.Mesh;
  private coreMat: THREE.ShaderMaterial;
  private geo: THREE.InstancedBufferGeometry;
  private angles = new Float32Array(NL);
  private axes: THREE.Vector3[] = [];
  private vp = new THREE.Matrix4();
  private rot = new THREE.Matrix4();
  private scaleV = new THREE.Vector3();
  private revs = new Float32Array(NL);
  private speedSm = 1;
  private warnSm = 0;
  private muteSm = 0;
  private tint = new THREE.Vector3(0.13, 0.83, 0.93);
  private w = 1;
  private h = 1;
  private time = 0;
  private buildStart = 0;
  private shockAt = -10;
  lost = false;

  constructor({ canvas, data }: OrbGLOptions) {
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: false,
      premultipliedAlpha: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setClearColor(0x000000, 0);
    canvas.addEventListener('webglcontextlost', (e) => {
      e.preventDefault();
      this.lost = true;
    });

    // ---- instanced ribbon geometry
    let nSeg = 0;
    for (const L of data.layers) {
      for (const s of L.strokes) {
        const n = s.p.length / 3;
        nSeg += s.c ? n : n - 1;
      }
    }
    const A = new Float32Array(nSeg * 3);
    const B = new Float32Array(nSeg * 3);
    const M = new Float32Array(nSeg * 4);
    let k = 0;
    let strokeId = 0;
    data.layers.slice(0, NL).forEach((L, li) => {
      this.axes.push(new THREE.Vector3(...L.axis).normalize());
      this.revs[li] = L.revs;
      for (const s of L.strokes) {
        const n = s.p.length / 3;
        const segs = s.c ? n : n - 1;
        const hsh = hash(strokeId++ + li * 977);
        // authored bevel depth (~0.001-0.003) -> relative ribbon thickness
        const wf = Math.max(0.6, Math.min(2.2, s.w / 0.0015));
        for (let i = 0; i < segs; i++) {
          const a = i * 3;
          const b = ((i + 1) % n) * 3;
          A[k * 3] = s.p[a];
          A[k * 3 + 1] = s.p[a + 1];
          A[k * 3 + 2] = s.p[a + 2];
          B[k * 3] = s.p[b];
          B[k * 3 + 1] = s.p[b + 1];
          B[k * 3 + 2] = s.p[b + 2];
          M[k * 4] = li;
          M[k * 4 + 1] = hsh;
          M[k * 4 + 2] = wf;
          M[k * 4 + 3] = i / Math.max(1, segs);
          k++;
        }
      }
    });
    this.geo = new THREE.InstancedBufferGeometry();
    this.geo.setAttribute(
      'aCorner',
      new THREE.BufferAttribute(new Float32Array([0, -1, 1, -1, 1, 1, 0, 1]), 2)
    );
    // `position` is required by three for draw-range; reuse corners
    this.geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(4 * 3), 3));
    this.geo.setIndex([0, 1, 2, 0, 2, 3]);
    this.geo.setAttribute('iA', new THREE.InstancedBufferAttribute(A, 3));
    this.geo.setAttribute('iB', new THREE.InstancedBufferAttribute(B, 3));
    this.geo.setAttribute('iM', new THREE.InstancedBufferAttribute(M, 4));
    this.geo.instanceCount = k;

    const arr = (n: number, v: number) => Array.from({ length: n }, () => v);
    this.mat = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneFactor,
      blendSrcAlpha: THREE.OneFactor,
      blendDstAlpha: THREE.OneFactor,
      uniforms: {
        uMVP: { value: Array.from({ length: NL }, () => new THREE.Matrix4()) },
        uCamZ: { value: 20 },
        uScale: { value: arr(NL, 1) },
        uReveal: { value: arr(NL, 1) },
        uRes: { value: new THREE.Vector2(1, 1) },
        uWidth: { value: 1.6 },
        uTime: { value: 0 },
        uTint: { value: this.tint },
        uAmber: { value: new THREE.Vector3(...AMBER) },
        uOff: { value: HUE_OFFSET.map((o) => (o * HUE_SPREAD * Math.PI) / 180 / 1) },
        uHot: { value: HOT },
        uSpread: { value: 1 },
        uWarn: { value: 0 },
        uMute: { value: 0 },
        uBoost: { value: 0 },
        uShock: { value: new THREE.Vector2(0, 0) },
        uGain: { value: 0.82 },
      },
    });
    const mesh = new THREE.Mesh(this.geo, this.mat);
    mesh.frustumCulled = false;
    this.scene.add(mesh);

    // ---- white-hot core bloom: one screen-space sprite, same additive blend
    this.coreMat = new THREE.ShaderMaterial({
      vertexShader: CORE_VERT,
      fragmentShader: /* glsl */ `
        uniform vec3 uTint; uniform float uAspect; uniform float uSize; uniform float uBoost; uniform float uWarn; uniform float uMute; uniform float uTime;
        varying vec2 vUv;
        void main() {
          float r = length(vUv * vec2(uAspect, 1.0)) / uSize;
          float g = exp(-r * r * 7.0) * 0.7 + exp(-r * r * 1.4) * 0.16;
          g *= 1.0 + uBoost * 0.8 + uWarn * 0.25 * sin(uTime * 3.4);
          vec3 c = mix(uTint, vec3(1.0), clamp(exp(-r * r * 22.0), 0.0, 1.0) * 0.85);
          c = mix(c, vec3(1.0, 0.6, 0.12), uWarn * 0.55 * smoothstep(0.0, 1.0, r));
          c = mix(c, vec3(dot(c, vec3(0.333))), uMute);
          vec3 rgb = c * g;
          gl_FragColor = vec4(rgb, clamp(max(rgb.r, max(rgb.g, rgb.b)), 0.0, 1.0));
        }`,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneFactor,
      blendSrcAlpha: THREE.OneFactor,
      blendDstAlpha: THREE.OneFactor,
      uniforms: {
        uTint: { value: this.tint },
        uSize: { value: 0.16 },
        uAspect: { value: 1 },
        uBoost: { value: 0 },
        uWarn: { value: 0 },
        uMute: { value: 0 },
        uTime: { value: 0 },
      },
    });
    this.core = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.coreMat);
    this.core.frustumCulled = false;
    this.core.renderOrder = -1;
    this.scene.add(this.core);

    this.buildStart = performance.now();
  }

  /** Match the canvas backing store to its CSS box. Returns false when empty. */
  resize(): boolean {
    const c = this.renderer.domElement;
    const rect = c.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = w;
    this.h = h;
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(w, h, false);
    const R = (Math.min(w, h) / 2) * 0.92;
    // visible half-extent at the origin plane so ORB_R maps to R px
    const half = (ORB_R / R) * (h / 2);
    const fov = 14;
    const dist = half / Math.tan((fov / 2) * (Math.PI / 180));
    this.camera.fov = fov;
    this.camera.aspect = w / h;
    this.camera.position.set(0, 0, dist);
    this.camera.near = Math.max(1, dist - 8);
    this.camera.far = dist + 8;
    this.camera.updateProjectionMatrix();
    this.camera.lookAt(0, 0, 0);
    this.camera.updateMatrixWorld(true);
    (this.mat.uniforms.uRes.value as THREE.Vector2).set(w * dpr, h * dpr);
    this.mat.uniforms.uWidth.value = Math.max(1.2, 1.2 * dpr * Math.min(1.4, R / 260));
    // core sprite: normalised so size is a fraction of the orb radius
    this.coreMat.uniforms.uSize.value = 0.1 * (R / (h / 2));
    this.coreMat.uniforms.uAspect.value = w / h;
    return true;
  }

  /** Restart the ring-by-ring assembly (HUD appear / intro land). */
  rebuild(now = performance.now()): void {
    this.buildStart = now;
  }

  /** Radial shockwave (wake and every state change). */
  shock(now = performance.now()): void {
    this.shockAt = now;
  }

  /** Advance by `dtMs` and draw. */
  frame(dtMs: number, inp: OrbInputs, now = performance.now(), draw = true): void {
    if (this.lost || this.w < 2) return;
    const dt = Math.min(0.05, dtMs / 1000);
    const u = this.mat.uniforms;
    const energy = Math.max(0.03, inp.energy);
    // smooth the speed so a state change eases rather than snaps the spin
    const targetSpeed = (inp.muted ? 0.35 : inp.speed) * energy;
    this.speedSm += (targetSpeed - this.speedSm) * Math.min(1, dt * 3.2);
    if (inp.reduced) {
      this.speedSm = 0;
    } else {
      this.time += dt * (0.4 + 0.6 * energy);
    }
    for (let i = 0; i < NL; i++) {
      this.angles[i] += dt * TAU * (this.revs[i] / LOOP_S) * BASE * this.speedSm;
      this.angles[i] %= TAU * 1000;
    }
    const tgt = hexToRgb(inp.color);
    const kc = Math.min(1, dt * 5);
    this.tint.x += (tgt[0] - this.tint.x) * kc;
    this.tint.y += (tgt[1] - this.tint.y) * kc;
    this.tint.z += (tgt[2] - this.tint.z) * kc;
    this.warnSm += (inp.warn - this.warnSm) * Math.min(1, dt * 2.2);
    this.muteSm += ((inp.muted ? 1 : 0) - this.muteSm) * Math.min(1, dt * 4);

    // assembly: shells reveal from the core outward
    const bt = inp.reduced ? 9 : (now - this.buildStart) / 1000;
    const scale = u.uScale.value as number[];
    const reveal = u.uReveal.value as number[];
    for (let i = 0; i < NL; i++) {
      const r = Math.max(0, Math.min(1, (bt - i * 0.1) / 0.7));
      const e = r * r * (3 - 2 * r);
      reveal[i] = e;
      scale[i] = 0.55 + 0.45 * e + inp.boost * 0.012 * (1 + i * 0.05);
    }
    // one projection*view*rotation*scale matrix per layer: the vertex stage does a
    // single mat4 * vec4 per endpoint instead of axis-angle trig + projection
    const mvp = u.uMVP.value as THREE.Matrix4[];
    this.vp.multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse);
    for (let i = 0; i < NL; i++) {
      this.rot.makeRotationAxis(this.axes[i], this.angles[i]);
      this.rot.scale(this.scaleV.setScalar(scale[i]));
      mvp[i].multiplyMatrices(this.vp, this.rot);
    }
    u.uCamZ.value = this.camera.position.z;

    const since = (now - this.shockAt) / 1000;
    const shock = u.uShock.value as THREE.Vector2;
    if (since < 0.9) shock.set(0.3 + since * 1.4, (1 - since / 0.9) * 0.9);
    else shock.set(0, 0);
    const boost = inp.boost + (since < 1 ? 1.1 * Math.exp(-since * 3) : 0);

    u.uTime.value = this.time;
    u.uWarn.value = this.warnSm;
    u.uMute.value = this.muteSm;
    u.uBoost.value = boost;
    const cu = this.coreMat.uniforms;
    cu.uTime.value = this.time;
    cu.uBoost.value = boost;
    cu.uWarn.value = this.warnSm;
    cu.uMute.value = this.muteSm;

    if (draw) this.renderer.render(this.scene, this.camera);
  }

  dispose(): void {
    this.geo.dispose();
    this.mat.dispose();
    this.coreMat.dispose();
    this.core.geometry.dispose();
    this.renderer.dispose();
  }
}
