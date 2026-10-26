// LOCKE//FX page wipe. ASCII glyph dissolve overlay. Self-contained.
(() => {
  'use strict';
  if (window.top !== window || document.contentType !== 'text/html') return;
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (RM) return;
  const RAMP = ' .·:-=+*#%▓█';
  const NOISE = '▓▒░#%&@$01<>/\\=+*';
  const CW = 10, CH = 18, BAND = 0.38;
  const cfg = { enabled: true, duration: 450, disabledHosts: [] };
  let host, cv, ctx, atlas, cols, rows, cw, ch, dpr, W, H, jit;
  let raf = 0, killT = 0, alive = false, G = [], seq = 0;

  function glyphs() {
    G = Array.from(new Set((NOISE + RAMP).replace(' ', '')));
  }
  function buildAtlas() {
    const cs = ['#5fe3ff', '#eafcff', '#ffb347', '#1b6f85'];
    atlas = document.createElement('canvas');
    atlas.width = G.length * cw; atlas.height = cs.length * ch;
    const a = atlas.getContext('2d');
    a.font = `${Math.round(ch * 0.78)}px ui-monospace,"DejaVu Sans Mono",monospace`;
    a.textAlign = 'center'; a.textBaseline = 'middle';
    cs.forEach((c, r) => { a.fillStyle = c; G.forEach((g, i) => a.fillText(g, i * cw + cw / 2, r * ch + ch / 2 + 1)); });
  }
  function mount() {
    host = document.createElement('div');
    host.style.cssText = 'all:initial;position:fixed;inset:0;z-index:2147483647;pointer-events:none;contain:strict';
    const sh = host.attachShadow({ mode: 'closed' });
    cv = document.createElement('canvas');
    cv.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;display:block';
    sh.appendChild(cv);
    (document.documentElement || document).appendChild(host);
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = innerWidth || 1280; H = innerHeight || 720;
    cw = Math.round(CW * dpr); ch = Math.round(CH * dpr);
    cols = Math.ceil(W / CW); rows = Math.ceil(H / CH);
    cv.width = cols * cw; cv.height = rows * ch;
    ctx = cv.getContext('2d');
    glyphs(); buildAtlas();
    jit = new Float32Array(cols * rows);
    for (let i = 0; i < jit.length; i++) jit[i] = Math.random();
    alive = true;
  }
  // prog: 0 solid .. 1 clear
  function draw(prog, t) {
    ctx.clearRect(0, 0, cv.width, cv.height);
    const nG = G.length, nN = NOISE.length, tick = (t / 33) | 0;
    const rampG = []; for (const c of RAMP) rampG.push(G.indexOf(c));
    const edge = prog * (1 + BAND);
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const k = y * cols + x;
        const d = (x / cols) * 0.62 + (y / rows) * 0.38 + jit[k] * 0.06;
        const u = (edge - d) / BAND;           // <0 solid, 0..1 dissolving, >=1 gone
        if (u >= 1) continue;
        const px = x * cw, py = y * ch;
        let gi, row, alpha;
        if (u < 0) {
          ctx.fillStyle = '#02090f'; ctx.fillRect(px, py, cw, ch);
          if (jit[k] < 0.55) continue;
          gi = (k * 7 + (jit[k] > 0.9 ? tick : 0)) % nN; row = 3; alpha = 0.5;
          ctx.globalAlpha = alpha; ctx.drawImage(atlas, G.indexOf(NOISE[gi]) * cw, row * ch, cw, ch, px, py, cw, ch);
          ctx.globalAlpha = 1; continue;
        }
        ctx.globalAlpha = Math.min(1, (1 - u) * 1.25);
        ctx.fillStyle = '#02090f'; ctx.fillRect(px, py, cw, ch);
        ctx.globalAlpha = 1;
        if (u < 0.45) {
          gi = G.indexOf(NOISE[(k * 13 + tick * 3 + (jit[k] * 97 | 0)) % nN]);
          row = u < 0.1 ? 1 : (jit[k] > 0.97 ? 2 : 0); alpha = 1;
        } else {
          const r = Math.min(RAMP.length - 1, Math.floor((1 - (u - 0.45) / 0.55) * (RAMP.length - 1)));
          gi = rampG[r]; if (gi < 0) continue;
          row = jit[k] > 0.985 ? 2 : 0; alpha = 1 - (u - 0.45) / 0.55;
        }
        ctx.globalAlpha = alpha;
        ctx.drawImage(atlas, gi * cw, row * ch, cw, ch, px, py, cw, ch);
      }
    }
    ctx.globalAlpha = 1;
  }
  function remove() {
    alive = false; cancelAnimationFrame(raf); clearTimeout(killT);
    if (host) host.remove(); host = cv = ctx = atlas = null;
  }
  // dir 'out': solid->clear, 'in': clear->solid and hold
  function play(dir, dur) {
    const my = ++seq; let t0 = 0;
    const step = (now) => {
      if (my !== seq || !alive) return;
      if (!t0) t0 = now;
      const d = dir === 'out' ? cfg.duration : dur;
      let p = Math.min(1, (now - t0) / (dir === 'out' && dur ? dur : d));
      draw(dir === 'out' ? p : 1 - p, now);
      if (p < 1) raf = requestAnimationFrame(step);
      else if (dir === 'out') remove();
    };
    raf = requestAnimationFrame(step);
  }
  function start(dur) {
    if (!alive) return;
    clearTimeout(killT); killT = setTimeout(remove, 1500);
    draw(0, 0);
    play('out', dur);
  }
  function boot(dur, first) {
    if (host) remove();
    mount(); draw(0, 0);
    clearTimeout(killT); killT = setTimeout(remove, 1500);
    let go = false;
    const fire = () => { if (go) return; go = true; start(dur); };
    if (first && document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fire, { once: true });
      setTimeout(fire, 320);
    } else requestAnimationFrame(fire);
  }

  try { boot(0, true); } catch (e) { try { remove(); } catch (_) {} return; }

  try {
    chrome.storage.local.get('lockeFx', (r) => {
      const s = r && r.lockeFx; if (!s) return;
      if (typeof s.enabled === 'boolean') cfg.enabled = s.enabled;
      if (+s.duration) cfg.duration = Math.max(200, Math.min(900, +s.duration));
      if (Array.isArray(s.disabledHosts)) cfg.disabledHosts = s.disabledHosts;
      if (!cfg.enabled || cfg.disabledHosts.includes(location.hostname)) remove();
    });
  } catch (_) {}

  addEventListener('pageshow', (e) => {
    if (!e.persisted || !cfg.enabled || cfg.disabledHosts.includes(location.hostname)) return;
    try { boot(200, false); } catch (_) { remove(); }
  });

  addEventListener('click', (e) => {
    try {
      if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      if (!cfg.enabled || cfg.disabledHosts.includes(location.hostname)) return;
      const a = e.target && e.target.closest && e.target.closest('a[href]');
      if (!a || a.hasAttribute('download')) return;
      const t = a.target; if (t && t !== '_self') return;
      const u = new URL(a.href, location.href);
      if (!/^https?:$/.test(u.protocol)) return;
      if (u.href.split('#')[0] === location.href.split('#')[0] && u.hash) return;
      if (host) remove();
      mount(); clearTimeout(killT); killT = setTimeout(remove, 1500);
      play('in', 180);
    } catch (_) { remove(); }
  }, true);
})();
