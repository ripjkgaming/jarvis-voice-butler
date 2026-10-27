import { subscribeAmbientFrame } from '../ambient-particles';
import { invoke, isTauri } from '../tauri';
import { particleSkin } from './particle-skin';
import { getCharacterState, prefersReduced, subscribeCharacters } from './store';

/** Full-HUD glow layer: ONE fullscreen-triangle draw call per frame, no
 *  textures, no per-frame allocation. The fragment shader evaluates orb bloom,
 *  soft radial light and panel-edge glow analytically (box SDFs) at a fraction
 *  of the HUD resolution (_motion.yaml glow.buffer_scale), with a subtle
 *  chromatic split on the edges. WebGL2 only; otherwise a one-sprite 2D path.
 *  Kill switches: setting (jarvis.cx.gl), env JARVIS_FX_GL=0 (via the shell),
 *  and a measured budget (glow.budget_ms) that switches the layer off itself. */

const MAX_BOXES = 12;
const PROBE_EVERY = 10;
const IDLE_HZ = 30;
const BUSY_HZ = 60;
const ENERGY: Record<string, number> = { idle: 0.35, listening: 0.7, thinking: 0.6, speaking: 1 };

const VERT = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision mediump float;
uniform vec2 uRes;
uniform float uScale, uTime, uGlow, uEdge, uEnergy, uLight;
uniform vec3 uAcc, uHot, uOrb;
uniform vec4 uBox[${MAX_BOXES}];
uniform int uN;
out vec4 o;

float sdBox(vec2 p, vec4 b) {
  vec2 d = abs(p - (b.xy + b.zw * 0.5)) - b.zw * 0.5;
  return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}

void main() {
  vec2 p = gl_FragCoord.xy / uScale;
  p.y = uRes.y - p.y;
  vec2 q = p - uOrb.xy;
  float d = length(q) / uOrb.z;
  float ang = atan(q.y, q.x);
  // bloom: tight core + wide halo, breathing with the voice energy
  float breathe = 1.0 + 0.06 * sin(uTime * 1.3) * uEnergy;
  float core = exp(-d * d * 2.2);
  float halo = exp(-max(d - 0.9, 0.0) * 1.7) * smoothstep(0.0, 0.9, d);
  // soft volumetric light: slow radial shafts that fade with distance
  float shafts = 0.5 + 0.5 * sin(ang * 5.0 + uTime * 0.18 + sin(ang * 3.0 - uTime * 0.11) * 1.4);
  float vol = shafts * exp(-d * 0.75) * 0.55;
  float orb = (core * 0.55 + halo * 0.32 + vol * 0.2) * uEnergy * breathe;
  // panel edge glow with a chromatic split (r/b sampled at +-edge)
  vec3 ed = vec3(0.0);
  float split = 1.0 + 2.5 * uEdge;
  for (int i = 0; i < ${MAX_BOXES}; i++) {
    if (i >= uN) break;
    float sd = max(sdBox(p, uBox[i]), 0.0);
    ed += vec3(exp(-(sd + split) * 0.16), exp(-sd * 0.16), exp(-(sd - split * 0.5 + 1.5) * 0.16)) * 0.09;
  }
  vec3 col = mix(uAcc, uHot, clamp(core * 1.4, 0.0, 1.0)) * orb + uAcc * ed * (0.5 + uEnergy * 0.5);
  col *= uGlow;
  float a = clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0);
  // light skins tint (normal blend); dark skins add (alpha ~ quarter)
  float k = mix(0.28, 1.0, uLight);
  // dither against banding
  float n = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) / 255.0;
  o = vec4(col * mix(1.0, 0.55, uLight) + n, a * k + n);
}`;

type Mode = 'gl' | '2d' | 'off';
type Gl = WebGL2RenderingContext;

export type GlowStats = { mode: Mode; probes: number[]; p50: number; killed: string | null };

export class GlowLayer {
  private canvas: HTMLCanvasElement | null = null;
  private gl: Gl | null = null;
  private g2: CanvasRenderingContext2D | null = null;
  private sprite: HTMLCanvasElement | null = null;
  private spriteVer = -1;
  private prog: WebGLProgram | null = null;
  private u: Record<string, WebGLUniformLocation | null> = {};
  private boxes = new Float32Array(MAX_BOXES * 4);
  private nBox = 0;
  private orb = new Float32Array(3);
  private rootEl: HTMLElement | null = null;
  private energy = ENERGY.idle;
  private energyNow = ENERGY.idle;
  private unsub: (() => void) | null = null;
  private unstore: (() => void) | null = null;
  private obs: MutationObserver | null = null;
  private ro: ResizeObserver | null = null;
  private frame = 0;
  private acc = 0;
  private t = 0;
  private ring: number[] = [];
  private ringMax = 9;
  private scale = 0.5;
  private budget = 2;
  private px = new Uint8Array(4);
  private layoutDirty = true;
  private layoutTimer = 0;
  private cssW = 0;
  private cssH = 0;
  killed: string | null = null;
  mode: Mode = 'off';
  readonly stats: GlowStats = { mode: 'off', probes: [], p50: 0, killed: null };

  start(): () => void {
    this.unstore = subscribeCharacters(() => this.sync());
    document.addEventListener('visibilitychange', this.sync);
    window.addEventListener('resize', this.markLayout);
    this.obs = new MutationObserver(this.onMutate);
    this.obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    void this.envGate().then(() => this.sync());
    this.sync();
    return () => this.stop();
  }

  stop() {
    this.pause();
    this.unstore?.();
    this.obs?.disconnect();
    this.ro?.disconnect();
    document.removeEventListener('visibilitychange', this.sync);
    window.removeEventListener('resize', this.markLayout);
    clearTimeout(this.layoutTimer);
    this.dropCanvas();
  }

  /** JARVIS_FX_GL=0 in the shell: the webview cannot read env, ask Rust. */
  private async envGate() {
    if (!isTauri()) return;
    try {
      const ok = await invoke<boolean>('fx_gl_enabled');
      if (!ok) this.kill('env');
    } catch {
      /* older shell without the command: setting and budget still guard */
    }
  }

  /** Hard kills (setting, env) remove the layer. Soft ones (no WebGL2, over
   *  budget, context lost) drop to the one-sprite 2D path. */
  private kill(reason: string) {
    if (this.killed === reason || (this.killed && this.mode !== 'gl')) return;
    this.killed = reason;
    this.stats.killed = reason;
    this.dropCanvas();
    this.sync();
  }

  private get hardKilled(): boolean {
    return this.killed === 'env' || this.killed === 'setting';
  }

  private onMutate = () => {
    this.markLayout();
    this.sync();
  };

  private markLayout = () => {
    this.layoutDirty = true;
  };

  /** Should the layer run right now? Hidden window, reduced motion and
   *  effects-off all stop it; nothing renders while paused. */
  private wanted(): boolean {
    const st = getCharacterState();
    const root = document.documentElement;
    return (
      st.effects &&
      !prefersReduced() &&
      !document.hidden &&
      !root.classList.contains('hud-hidden') &&
      !root.classList.contains('school-tx') &&
      !root.classList.contains('suit-open')
    );
  }

  private sync = () => {
    const root = document.querySelector<HTMLElement>('[data-jstate]');
    if (root !== this.rootEl) {
      this.rootEl = root;
      this.ro?.disconnect();
      if (root) {
        this.ro = new ResizeObserver(this.markLayout);
        this.ro.observe(root);
      }
    }
    if (!getCharacterState().gl && !this.killed) this.kill('setting');
    if (!root || !this.wanted() || this.hardKilled) {
      this.pause();
      return;
    }
    if (this.mode === 'off') this.open();
    if (this.mode === 'off') return;
    if (!this.unsub) {
      this.acc = 0;
      this.unsub = subscribeAmbientFrame(this.draw);
    }
    if (this.canvas) this.canvas.style.display = 'block';
  };

  private pause() {
    this.unsub?.();
    this.unsub = null;
    if (this.canvas) this.canvas.style.display = 'none';
  }

  private open() {
    const st = getCharacterState();
    const cfg = st.motion?.glow;
    this.scale = cfg?.buffer_scale ?? 0.5;
    this.budget = cfg?.budget_ms ?? 2;
    this.ringMax = Math.max(3, Math.round((cfg?.budget_frames ?? 90) / 10));
    const c = document.createElement('canvas');
    c.setAttribute('aria-hidden', 'true');
    c.dataset.cxGlow = 'true';
    c.style.cssText =
      'position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:12;display:none';
    let ctx: Gl | null = null;
    if (!this.killed) {
      try {
        ctx = c.getContext('webgl2', {
          alpha: true,
          premultipliedAlpha: true,
          antialias: false,
          depth: false,
          stencil: false,
          powerPreference: 'low-power',
          preserveDrawingBuffer: false,
        }) as Gl | null;
      } catch {
        ctx = null;
      }
    }
    if (ctx && this.buildProgram(ctx)) {
      this.gl = ctx;
      this.mode = 'gl';
      c.addEventListener('webglcontextlost', (e) => {
        e.preventDefault();
        if (this.canvas === c) this.kill('context-lost');
      });
    } else {
      // No WebGL2 (or switched off): the CPU-safe sprite path on a fresh 2D canvas.
      const c2 = document.createElement('canvas');
      c2.setAttribute('aria-hidden', 'true');
      c2.dataset.cxGlow = 'true';
      c2.style.cssText = c.style.cssText;
      const g2 = c2.getContext('2d', { alpha: true });
      if (!g2) return;
      this.g2 = g2;
      this.canvas = c2;
      this.mode = '2d';
      document.body.appendChild(c2);
      this.stats.mode = '2d';
      this.layoutDirty = true;
      return;
    }
    this.canvas = c;
    document.body.appendChild(c);
    this.stats.mode = 'gl';
    this.layoutDirty = true;
  }

  private buildProgram(gl: Gl): boolean {
    const mk = (type: number, src: string) => {
      const sh = gl.createShader(type);
      if (!sh) return null;
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      return gl.getShaderParameter(sh, gl.COMPILE_STATUS) ? sh : null;
    };
    const vs = mk(gl.VERTEX_SHADER, VERT);
    const fs = mk(gl.FRAGMENT_SHADER, FRAG);
    const prog = gl.createProgram();
    if (!vs || !fs || !prog) return false;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
    this.prog = prog;
    gl.useProgram(prog);
    for (const n of [
      'uRes',
      'uScale',
      'uTime',
      'uGlow',
      'uEdge',
      'uEnergy',
      'uLight',
      'uAcc',
      'uHot',
      'uOrb',
      'uBox',
      'uN',
    ]) {
      this.u[n] = gl.getUniformLocation(prog, n);
    }
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0, 0, 0, 0);
    return true;
  }

  private dropCanvas() {
    const gl = this.gl;
    if (gl && this.prog) gl.deleteProgram(this.prog);
    const c = this.canvas;
    this.canvas = null;
    this.gl?.getExtension('WEBGL_lose_context')?.loseContext();
    c?.remove();
    this.canvas = null;
    this.gl = null;
    this.g2 = null;
    this.prog = null;
    this.mode = 'off';
    this.stats.mode = 'off';
  }

  /** DOM reads happen here only (resize, class changes), never per frame. */
  private measure() {
    this.layoutDirty = false;
    const root = this.rootEl;
    if (!root || !this.canvas) return;
    const w = window.innerWidth;
    const h = window.innerHeight;
    if (w !== this.cssW || h !== this.cssH) {
      this.cssW = w;
      this.cssH = h;
      this.canvas.width = Math.max(1, Math.round(w * this.scale));
      this.canvas.height = Math.max(1, Math.round(h * this.scale));
      this.gl?.viewport(0, 0, this.canvas.width, this.canvas.height);
    }
    const reactor = root.querySelector<HTMLElement>('[data-reactor-energy]')?.parentElement;
    const r = reactor?.getBoundingClientRect();
    if (r && r.width > 40) {
      this.orb[0] = r.left + r.width / 2;
      this.orb[1] = r.top + r.height / 2;
      this.orb[2] = Math.min(r.width, r.height) * 0.36;
    } else {
      this.orb[0] = w / 2;
      this.orb[1] = h / 2;
      this.orb[2] = Math.min(w, h) * 0.2;
    }
    let n = 0;
    const frames = root.querySelectorAll<HTMLElement>('[class*="frame"], header, footer');
    for (let i = 0; i < frames.length && n < MAX_BOXES; i++) {
      const b = frames[i].getBoundingClientRect();
      if (b.width < 24 || b.height < 16) continue;
      this.boxes[n * 4] = b.left;
      this.boxes[n * 4 + 1] = b.top;
      this.boxes[n * 4 + 2] = b.width;
      this.boxes[n * 4 + 3] = b.height;
      n++;
    }
    this.nBox = n;
    const s = root.dataset.jstate ?? 'idle';
    this.energy = ENERGY[s] ?? ENERGY.idle;
  }

  private draw = (delta: number) => {
    const canvas = this.canvas;
    if (!canvas) return;
    this.acc += delta;
    const hz = document.querySelector('[data-cx="run"]') ? BUSY_HZ : IDLE_HZ;
    if (this.acc < 1000 / hz - 1) return;
    this.t += this.acc / 1000;
    const dt = this.acc;
    this.acc = 0;
    if (this.layoutDirty) {
      clearTimeout(this.layoutTimer);
      this.layoutTimer = window.setTimeout(() => {
        this.measure();
      }, 0);
      this.layoutDirty = false;
      if (!this.cssW) this.measure();
    }
    this.energyNow += (this.energy - this.energyNow) * (1 - Math.exp(-dt / 400));
    if (this.mode === 'gl' && this.gl) this.drawGl(this.gl);
    else if (this.g2) this.draw2d(this.g2);
  };

  private drawGl(gl: Gl) {
    const u = this.u;
    const k = particleSkin;
    const light = k.alpha < 1 ? 1 : 0;
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(u.uRes, this.cssW, this.cssH);
    gl.uniform1f(u.uScale, this.scale);
    gl.uniform1f(u.uTime, this.t);
    gl.uniform1f(u.uGlow, k.glow);
    gl.uniform1f(u.uEdge, k.edge);
    gl.uniform1f(u.uEnergy, this.energyNow);
    gl.uniform1f(u.uLight, light);
    gl.uniform3f(u.uAcc, k.rgb[0] / 255, k.rgb[1] / 255, k.rgb[2] / 255);
    gl.uniform3f(u.uHot, k.hot[0] / 255, k.hot[1] / 255, k.hot[2] / 255);
    gl.uniform3fv(u.uOrb, this.orb);
    gl.uniform4fv(u.uBox, this.boxes);
    gl.uniform1i(u.uN, this.nBox);
    const probe = ++this.frame % PROBE_EVERY === 0;
    const t0 = probe ? performance.now() : 0;
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    if (probe) {
      // 1x1 readback forces the GPU to finish: wall time = true shader cost.
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, this.px);
      this.record(performance.now() - t0);
    }
  }

  private record(ms: number) {
    const r = this.ring;
    r.push(ms);
    if (r.length > this.ringMax) r.shift();
    const s = this.stats;
    s.probes = r.slice();
    const sorted = r.slice().sort((a, b) => a - b);
    s.p50 = sorted[sorted.length >> 1];
    // Config may arrive after the layer opened: read the budget fresh.
    const budget = getCharacterState().motion?.glow?.budget_ms ?? this.budget;
    if (r.length >= this.ringMax && s.p50 > budget) this.kill('budget');
  }

  /** CPU-safe path: one pre-baked radial sprite, blitted at the orb. */
  private draw2d(g: CanvasRenderingContext2D) {
    if (!this.cssW) this.measure();
    const k = particleSkin;
    if (this.spriteVer !== k.version) this.bakeSprite();
    const sp = this.sprite;
    if (!sp) return;
    g.setTransform(this.scale, 0, 0, this.scale, 0, 0);
    g.clearRect(0, 0, this.cssW, this.cssH);
    g.globalAlpha = Math.min(1, 0.6 * k.glow * this.energyNow * (k.alpha < 1 ? 0.5 : 1));
    const r = this.orb[2] * 3.2;
    g.drawImage(sp, this.orb[0] - r, this.orb[1] - r, r * 2, r * 2);
    g.globalAlpha = 1;
  }

  private bakeSprite() {
    const s = this.sprite ?? (this.sprite = document.createElement('canvas'));
    s.width = s.height = 128;
    const g = s.getContext('2d');
    if (!g) return;
    const k = particleSkin;
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    const rgb = `${k.rgb[0] | 0},${k.rgb[1] | 0},${k.rgb[2] | 0}`;
    grad.addColorStop(0, `rgba(${rgb},0.55)`);
    grad.addColorStop(0.35, `rgba(${rgb},0.2)`);
    grad.addColorStop(1, `rgba(${rgb},0)`);
    g.clearRect(0, 0, 128, 128);
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
    this.spriteVer = k.version;
  }
}

/** Test and recording hook: window.__fx.stats after a few seconds. */
export function exposeGlow(l: GlowLayer) {
  try {
    (window as unknown as { __fx?: GlowLayer }).__fx = l;
  } catch {
    /* non-browser */
  }
}
