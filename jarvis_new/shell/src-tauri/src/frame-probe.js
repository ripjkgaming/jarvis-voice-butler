// Opt-in, bounded, content-free native renderer timing diagnostic.
(() => {
  if (window.__jarvisFrameProbe?.running) return;
  const report = window.__jarvisFrameProbe = { running: true, phases: {}, transitions: [], fades: [] };
  const started = performance.now(); let last = 0, phase = '', changed = -1000, intervals = [];
  const snapshot = () => {
    const r = document.querySelector('.vlink-reticle');
    const o = document.querySelector('.vlink-reticle__orbit');
    const e = document.querySelector('[data-reactor-energy]');
    return {phase:r?.dataset.phase, reticle:r && getComputedStyle(r).opacity,
      orbit:o && getComputedStyle(o).transform, animation:o && getComputedStyle(o).animationName,
      energy:e && {display:getComputedStyle(e).display, opacity:getComputedStyle(e).opacity}};
  };
  const tick = now => {
    const current = snapshot();
    if (last) {const dt=now-last; intervals.push(dt); (report.phases[current.phase] ||= []).push(dt);}
    if (current.phase !== phase) { report.transitions.push({at:Math.round(now-started), ...current}); phase=current.phase; changed=now; }
    if (now-changed < 450) report.fades.push({at:Math.round(now-started), ...current});
    last=now;
    if (now-started < 20000 && !document.hidden) {requestAnimationFrame(tick); return;}
    const stats = xs => {xs.sort((a,b)=>a-b);return {samples:xs.length,median_ms:xs[Math.floor(xs.length*.5)]||0,p95_ms:xs[Math.floor(xs.length*.95)]||0,over_50ms:xs.filter(x=>x>50).length,max_ms:xs.at(-1)||0};};
    report.total=stats(intervals); for (const k in report.phases) report.phases[k]=stats(report.phases[k]);
    report.running=false;
  };
  requestAnimationFrame(tick);
})();
