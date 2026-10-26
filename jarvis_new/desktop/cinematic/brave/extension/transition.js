(() => {
  // Static and upgrade injection can race on newly loaded documents.
  if (globalThis.__jarvisMotionVersion === '1.2.0') return;
  globalThis.__jarvisMotionVersion = '1.2.0';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const kinds = new Set(['open', 'close', 'switch', 'loading', 'ready', 'pin', 'mute', 'move', 'zoom']);
  let host, timer, quiet = true, lastKind, lastTime = 0;

  function clear() {
    clearTimeout(timer);
    host?.remove();
    host = null;
  }

  function play(kind) {
    if (!kinds.has(kind) || quiet || reduced.matches || document.hidden || !document.documentElement) return;
    const now = performance.now();
    // Do not truncate an entrance when the page finishes loading immediately.
    if (now - lastTime < 750 && (kind === lastKind || (lastKind === 'open' && ['switch', 'ready'].includes(kind)))) return;
    clear();
    lastKind = kind;
    lastTime = now;
    host = document.createElement('div');
    host.dataset.jarvisMotion = kind;
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'all:initial!important;position:fixed!important;inset:0!important;z-index:2147483647!important;pointer-events:none!important;contain:strict!important;';
    const root = host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    // All motion stays at the perimeter. No page blur, tint, layout or input changes.
    style.textContent = `
      :host{pointer-events:none!important;color:#8bcaff;--time:900ms}
      *{box-sizing:border-box;pointer-events:none!important}
      .edge{position:absolute;inset:3px;border:1px solid #75bfffa0;box-shadow:inset 0 0 12px #438fff25;opacity:0;animation:edge var(--time) ease-out both}
      .rail{position:absolute;top:0;left:0;width:100%;height:3px;background:linear-gradient(90deg,transparent,#69b6ff,#d2efff,transparent);transform-origin:left;animation:rail var(--time) cubic-bezier(.16,1,.3,1) both}
      .corner{position:absolute;width:46px;height:46px;border:solid #a9dbff;border-width:2px 0 0 2px;animation:lock var(--time) ease-out both}
      .a{top:7px;left:7px}.b{top:7px;right:7px;rotate:90deg}.c{bottom:7px;right:7px;rotate:180deg}.d{bottom:7px;left:7px;rotate:270deg}
      .slice{position:absolute;right:3px;top:22%;width:18px;height:1px;background:#8acaff;animation:slice var(--time) ease-out both}.s2{top:74%;left:3px;right:auto;animation-delay:45ms}
      :host([data-jarvis-motion="close"]){--time:1000ms}
      :host([data-jarvis-motion="close"]) .corner{animation-name:release}
      :host([data-jarvis-motion="close"]) .rail{transform-origin:right;animation-name:retract}
      :host([data-jarvis-motion="switch"]){--time:600ms}
      :host([data-jarvis-motion="switch"]) .rail{animation-name:glide}
      :host([data-jarvis-motion="loading"]) .rail{animation-name:glide;--time:850ms}
      :host([data-jarvis-motion="loading"]) :is(.edge,.corner,.slice){display:none}
      :host([data-jarvis-motion="ready"]){--time:360ms}
      :host([data-jarvis-motion="ready"]) :is(.corner,.slice){display:none}
      :host([data-jarvis-motion="pin"]) .corner{animation-name:pin}
      :host([data-jarvis-motion="mute"]) .rail{transform-origin:center;animation-name:retract}
      :host([data-jarvis-motion="move"]) .rail{animation-name:glide}
      :host([data-jarvis-motion="zoom"]) .corner{animation-name:zoom}
      @keyframes edge{0%,100%{opacity:0}25%{opacity:.7}}
      @keyframes rail{0%{transform:scaleX(.03);opacity:0}22%{opacity:.9}75%,100%{transform:scaleX(1);opacity:0}}
      @keyframes lock{0%{translate:10px 10px;opacity:0}35%{translate:0 0;opacity:.8}100%{opacity:0}}
      @keyframes release{0%{opacity:0}20%{opacity:.7;translate:0 0}100%{opacity:0;translate:18px 12px;scale:.65}}
      @keyframes retract{0%{transform:scaleX(1);opacity:0}20%{opacity:.8}100%{transform:scaleX(.02);opacity:0}}
      @keyframes glide{0%{transform:translateX(-100%);opacity:0}20%{opacity:.8}100%{transform:translateX(100%);opacity:0}}
      @keyframes slice{0%,100%{opacity:0;transform:translateY(0) scaleX(.2)}30%{opacity:.5;transform:translateY(5px) scaleX(1)}65%{opacity:.2;transform:translateY(16px) scaleX(.5)}}
      @keyframes pin{0%{opacity:0;scale:1.4}35%{opacity:.8;scale:.85}65%{scale:1}100%{opacity:0}}
      @keyframes zoom{0%{opacity:0;translate:14px 14px}35%{opacity:.7}100%{opacity:0;translate:-4px -4px}}
      @media(prefers-reduced-motion:reduce){*{animation:none!important;display:none!important}}
    `;
    root.append(style);
    for (const name of ['edge', 'rail', 'corner a', 'corner b', 'corner c', 'corner d', 'slice', 'slice s2']) {
      const part = document.createElement('i');
      part.className = name;
      root.append(part);
    }
    document.documentElement.append(host);
    timer = setTimeout(clear, 1100);
  }

  chrome.runtime.onMessage.addListener(message => play(message.jarvisCue));
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes['jarvis-quiet']) {
      quiet = changes['jarvis-quiet'].newValue === true;
      if (quiet) clear();
    }
  });
  function reportReduced() {
    chrome.runtime.sendMessage({ jarvisMotionReduced: reduced.matches }).catch(() => {});
    if (reduced.matches) clear();
  }
  reduced.addEventListener('change', reportReduced);
  reportReduced();
  document.addEventListener('visibilitychange', () => { if (document.hidden) clear(); });
  addEventListener('pagehide', clear);
  // Read settings before playing anything, including on an already-loaded page.
  chrome.storage.local.get('jarvis-quiet').then(settings => {
    quiet = settings['jarvis-quiet'] === true;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => play('open'), { once: true });
    else play('open');
  }).catch(() => { /* If settings are unavailable, remain quiet. */ });
})();
