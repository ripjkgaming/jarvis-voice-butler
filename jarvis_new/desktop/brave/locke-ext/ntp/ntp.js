import * as A from '../lib/ascii.js';

const C = typeof chrome !== 'undefined' && chrome ? chrome : null;
const reduced = A.prefersReducedMotion();
const ACCENT = '#5fe3ff';
const $ = (s) => document.querySelector(s);
const pad2 = (n) => String(n).padStart(2, '0');
const T0 = performance.now();

/* ------------------------------------------------------------ helpers */
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const fav = (u) =>
  C && C.runtime && C.runtime.getURL
    ? C.runtime.getURL('/_favicon/?pageUrl=' + encodeURIComponent(u) + '&size=32')
    : null;
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return u || ''; } };
const label = (it) => (it.title && it.title.trim()) || host(it.url) || it.url || '';
function bytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / 1024 ** i;
  return (v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + u[i];
}
const guard = async (fn) => { try { return await fn(); } catch { return undefined; } };

/* ------------------------------------------------------------ frame loop */
const anims = new Set(); // fn(now) -> true when finished
let bgDraw = null;
let lastT = 0;
let rafId = 0;
function loop(now) {
  rafId = 0;
  if (document.hidden) return;
  if (now - lastT >= 33) {
    lastT = now;
    for (const a of [...anims]) if (a(now)) anims.delete(a);
    if (bgDraw) bgDraw(now);
  }
  if (!reduced || anims.size) rafId = requestAnimationFrame(loop);
}
function kick() { if (!rafId && !document.hidden) rafId = requestAnimationFrame(loop); }
document.addEventListener('visibilitychange', kick);

/* text-node decode / hover flicker */
function textNodes(root) {
  const out = [];
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) if (n.data.trim()) out.push(n);
  return out;
}
function decode(root, dur = 650, delay = 0) {
  if (reduced || !root) return;
  const nodes = textNodes(root);
  const orig = nodes.map((n) => n.data);
  const t0 = performance.now() + delay;
  nodes.forEach((n, i) => (n.data = A.scramble(orig[i], 0, i)));
  anims.add((now) => {
    const p = (now - t0) / dur;
    if (p < 0) return false;
    const done = p >= 1;
    nodes.forEach((n, i) => (n.data = done ? orig[i] : A.scramble(orig[i], p, i + Math.floor(now / 90))));
    return done;
  });
  kick();
}
function flicker(node) {
  if (reduced || !node || node.dataset.busy) return;
  const txt = node.textContent;
  if (!txt) return;
  node.dataset.busy = '1';
  const t0 = performance.now();
  anims.add((now) => {
    const p = (now - t0) / 260;
    const done = p >= 1;
    node.textContent = done ? txt : A.scramble(txt, p * p, Math.floor(now / 50));
    if (done) delete node.dataset.busy;
    return done;
  });
  kick();
}

/* ------------------------------------------------------------ metrics */
let cw = 8;
let ch = 17;
function measure() {
  const r = $('#probe').getBoundingClientRect();
  cw = r.width / $('#probe').textContent.length || 8;
  ch = parseFloat(getComputedStyle(document.body).lineHeight) || 17;
}

/* ------------------------------------------------------------ panels */
const panels = [];
function makePanel(id, col, title, rowsFn, opt = {}) {
  const root = el('section', 'panel' + (opt.center ? ' center' : ''));
  root.id = id;
  root.hidden = true;
  const head = el('div', 'ph');
  const body = el('div', 'pb');
  const foot = el('div', 'pf');
  root.append(head, body, foot);
  $(col).append(root);
  const p = { id, root, head, body, foot, title, rowsFn, opt, cols: 0, inner: 0, shown: false, meta: '', data: null };
  panels.push(p);
  return p;
}
function layoutPanel(p) {
  const parent = p.root.parentElement;
  const avail = Math.floor(parent.clientWidth / cw);
  const cols = Math.max(12, Math.min(p.opt.maxCols || 999, avail));
  p.cols = cols;
  p.inner = cols - 4;
  p.root.style.width = cols * cw + 'px';
  const title = `┌─[ ${p.title} ]`;
  const meta = p.meta ? `[ ${p.meta} ]─` : '';
  const fill = Math.max(0, cols - title.length - meta.length - 1);
  p.head.replaceChildren();
  p.head.append(title.slice(0, 3), Object.assign(el('b'), { textContent: title.slice(3) }), '─'.repeat(fill), meta, '┐');
  p.foot.textContent = '└' + '─'.repeat(cols - 2) + '┘';
}
function segNode(s) {
  if (typeof s === 'string') return document.createTextNode(s);
  if (s.node) return s.node;
  let n;
  if (s.href || s.restore) {
    n = el('a', 'lnk' + (s.w ? ' tile' : ''));
    if (s.href) { n.href = s.href; n.dataset.href = s.href; }
    if (s.restore) n.dataset.restore = s.restore;
    if (s.key) n.append(el('span', 'key', s.key));
    const f = s.fav && fav(s.fav);
    if (f) {
      const im = el('img');
      im.alt = '';
      im.src = f;
      im.addEventListener('error', () => im.remove());
      n.append(im);
    }
    n.append(el('span', 'lbl', s.t));
    if (s.w) n.style.width = s.w + 'ch';
    if (s.title) n.title = s.title;
  } else if (s.bar) {
    n = el('span');
    n.append(el('span', s.c || 'on', s.bar[0]), el('span', 'off', s.bar[1]));
  } else {
    n = el('span', (s.c || '') + (s.fill ? ' t' : ''), s.t);
    if (s.fill) n.style.flex = '1';
  }
  return n;
}
function drawPanel(p) {
  if (!p.data && !p.opt.always) return;
  layoutPanel(p);
  const rows = p.rowsFn(p.inner, p.data) || [];
  const frag = document.createDocumentFragment();
  for (const segs of rows) {
    const r = el('div', 'r');
    const c = el('span', 'c');
    for (const s of Array.isArray(segs) ? segs : [segs]) c.append(segNode(s));
    r.append(el('i', null, '│'), c, el('i', null, '│'));
    frag.append(r);
  }
  p.body.replaceChildren(frag);
  layoutPanel(p);
  if (!p.shown) {
    p.shown = true;
    p.root.hidden = false;
    if (!reduced) {
      const idx = panels.indexOf(p);
      const d = Math.min(450, 80 + idx * 40);
      p.root.style.opacity = '0';
      setTimeout(() => (p.root.style.opacity = ''), d);
      decode(p.root, 700, d);
    }
  }
}
function setData(p, data, meta) {
  p.data = data;
  if (meta !== undefined) p.meta = meta;
  drawPanel(p);
}

/* ------------------------------------------------------------ panels: layout */
const search = makePanel('search', '#searchSlot', 'LOCKE//QUERY', null, { always: true, center: true, maxCols: 64 });
const pHist = makePanel('history', '#colL', 'LOCKE//TRACE', null);
const pAct = makePanel('activity', '#colL', 'LOCKE//PULSE 24H', null);
const pClosed = makePanel('closed', '#colL', 'LOCKE//RESTORE', null);
const pSites = makePanel('sites', '#colM', 'LOCKE//NET', null, { center: true, maxCols: 92 });
const pCore = makePanel('core', '#colR', 'LOCKE//CORE', null);
const pMem = makePanel('mem', '#colR', 'LOCKE//MEM', null);
const pSys = makePanel('sys', '#colR', 'LOCKE//SYS', null, { always: true });
const pBm = makePanel('bookmarks', '#colR', 'LOCKE//MARKS', null);
const pDl = makePanel('downloads', '#colR', 'LOCKE//DROP', null);

/* ------------------------------------------------------------ navigation + wipe-out */
const wipeC = $('#wipe');
let atlas = null;
let W = 0;
let H = 0;
let dpr = 1;
let gcw = 8;
let gch = 13;
function wipeOut(cb) {
  if (reduced || !atlas) return cb();
  const ctx = wipeC.getContext('2d');
  const t0 = performance.now();
  const cols = Math.ceil(W / gcw);
  const rows = Math.ceil(H / gch);
  let fired = false;
  anims.add((now) => {
    const p = Math.min(1, (now - t0) / 250);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#02090f';
    ctx.fillRect(0, 0, W, Math.min(H, p * 1.25 * H));
    const front = p * 1.25 * rows;
    const seed = Math.floor(now / 40);
    for (let y = 0; y < rows; y++) {
      if (y > front) break;
      const lvl = A.clamp(1 - (front - y) / (rows * 0.25));
      for (let x = 0; x < cols; x++) {
        atlas.draw(ctx, A.noiseIndex(A.hash(x + seed, y)), A.clamp(0.25 + lvl * 0.75 * A.hash(x, y + seed)), x * gcw, y * gch);
      }
    }
    if (p >= 1 && !fired) { fired = true; cb(); }
    return p >= 1;
  });
  kick();
}
function go(url) {
  wipeOut(() => {
    if (C && C.tabs && C.tabs.update) {
      C.tabs.update({ url }).catch(() => (location.href = url));
    } else location.href = url;
  });
}
function looksLikeUrl(s) {
  if (/\s/.test(s)) return false;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return true;
  if (/^(localhost|(\d{1,3}\.){3}\d{1,3})(:\d+)?(\/|$)/i.test(s)) return true;
  return /^[^\s/]+\.[a-z]{2,}(:\d+)?([/?#].*)?$/i.test(s);
}
function submit(q) {
  q = q.trim();
  if (!q) return;
  if (looksLikeUrl(q)) go(/^[a-z][a-z0-9+.-]*:\/\//i.test(q) ? q : 'https://' + q);
  else go('https://search.brave.com/search?q=' + encodeURIComponent(q));
}

document.addEventListener('click', (e) => {
  const a = e.target.closest && e.target.closest('a.lnk');
  if (!a) return;
  if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
  e.preventDefault();
  if (a.dataset.restore) {
    const id = a.dataset.restore;
    wipeOut(() => guard(() => C.sessions.restore(id)));
    return;
  }
  go(a.dataset.href);
});
document.addEventListener('mouseover', (e) => {
  const a = e.target.closest && e.target.closest('a.lnk');
  if (a) flicker(a.querySelector('.lbl'));
});

/* ------------------------------------------------------------ search panel */
const input = el('input');
input.type = 'text';
input.autocomplete = 'off';
input.spellcheck = false;
input.autofocus = true;
input.setAttribute('aria-label', 'Search or enter address');
const typed = el('span', 'typed');
const cur = el('span', 'cur');
search.rowsFn = (inner) => {
  const w = el('span');
  w.append(el('span', 'pr', 'locke>'), typed, cur, input);
  return [[{ node: w }]];
};
search.rowsFn.always = true;
function syncTyped() {
  typed.textContent = input.value;
  cur.style.marginLeft = '0';
}
input.addEventListener('input', syncTyped);
input.addEventListener('focus', () => search.root.classList.remove('blur'));
input.addEventListener('blur', () => search.root.classList.add('blur'));
search.root.id = 'search';
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') submit(input.value);
  else if (e.key === 'Escape') input.blur();
});
search.root.addEventListener('mousedown', () => setTimeout(() => input.focus(), 0));
document.addEventListener('keydown', (e) => {
  if (e.target === input || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '/') { e.preventDefault(); input.focus(); return; }
  if (/^[1-9]$/.test(e.key)) {
    const s = sitesList[+e.key - 1];
    if (s) { e.preventDefault(); go(s.url); }
  }
});

/* ------------------------------------------------------------ data: top sites */
let sitesList = [];
pSites.rowsFn = (inner, list) => {
  const tw = 22;
  const per = Math.max(1, Math.floor(inner / tw));
  const w = Math.floor(inner / per);
  const rows = [];
  for (let i = 0; i < list.length; i += per) {
    rows.push(list.slice(i, i + per).map((s, k) => ({ href: s.url, t: label(s), fav: s.url, key: i + k < 9 ? String(i + k + 1) : '·', w, title: s.url })));
  }
  return rows;
};
async function loadSites() {
  if (!C || !C.topSites) return;
  const list = await guard(() => C.topSites.get());
  if (list && list.length) {
    sitesList = list.slice(0, 10);
    setData(pSites, sitesList, String(sitesList.length));
  }
}

/* ------------------------------------------------------------ data: history */
pHist.rowsFn = (inner, list) =>
  list.map((h) => {
    const d = new Date(h.lastVisitTime);
    return [{ c: 'dim', t: `${pad2(d.getHours())}:${pad2(d.getMinutes())} ` }, { href: h.url, t: label(h), fav: h.url, title: h.url }];
  });
async function loadHistory() {
  if (!C || !C.history) return;
  const list = await guard(() => C.history.search({ text: '', maxResults: 12, startTime: 0 }));
  if (list && list.length) setData(pHist, list.slice(0, 12), String(Math.min(12, list.length)));
}

/* ------------------------------------------------------------ data: 24h activity */
pAct.rowsFn = (inner, d) => {
  const k = Math.max(1, Math.floor(inner / 24));
  const vals = d.counts.flatMap((v) => Array(k).fill(v));
  const lines = A.blockChart(vals, vals.length, 4, 0, Math.max(1, d.peak));
  const rows = lines.map((l) => [{ c: 'on', t: l }]);
  const axis = A.fit('-24h', vals.length - 3) + 'now';
  rows.push([{ c: 'dim', t: axis }]);
  const ph = d.peakIdx;
  rows.push([{ c: 'ice', t: `${d.total} visits` }, { c: 'dim', t: '  peak ' }, { c: 'am', t: `${pad2(ph)}:00 ×${d.peak}` }]);
  return rows;
};
async function loadActivity() {
  if (!C || !C.history) return;
  const now = Date.now();
  const items = await guard(() => C.history.search({ text: '', startTime: now - 864e5, maxResults: 1500 }));
  if (!items) return;
  const counts = new Array(24).fill(0);
  const bump = (t) => {
    const age = now - t;
    if (age >= 0 && age < 864e5) counts[23 - Math.floor(age / 36e5)]++;
  };
  const head = items.slice(0, 250);
  items.slice(250).forEach((i) => bump(i.lastVisitTime));
  await Promise.all(head.map(async (it) => {
    const v = await guard(() => C.history.getVisits({ url: it.url }));
    if (v && v.length) v.forEach((x) => bump(x.visitTime));
    else bump(it.lastVisitTime);
  }));
  const total = counts.reduce((a, b) => a + b, 0);
  const peak = Math.max(...counts);
  const hourOf = (i) => new Date(now - (23 - i) * 36e5).getHours();
  const peakIdx = hourOf(counts.indexOf(peak));
  setData(pAct, { counts, total, peak, peakIdx }, '24h');
}

/* ------------------------------------------------------------ data: closed sessions */
pClosed.rowsFn = (inner, list) =>
  list.map((e) => {
    if (e.tab) return [{ c: 'dim', t: '↺ ' }, { restore: e.tab.sessionId, t: label(e.tab), fav: e.tab.url }];
    const n = (e.window.tabs || []).length;
    return [{ c: 'am', t: '▣ ' }, { restore: e.window.sessionId, t: `window · ${n} tab${n === 1 ? '' : 's'}` }];
  });
async function loadClosed() {
  if (!C || !C.sessions) return;
  const list = await guard(() => C.sessions.getRecentlyClosed({ maxResults: 8 }));
  const ok = (list || []).filter((e) => (e.tab && e.tab.sessionId) || (e.window && e.window.sessionId));
  if (ok.length) setData(pClosed, ok, String(ok.length));
}

/* ------------------------------------------------------------ data: bookmarks */
pBm.rowsFn = (inner, list) => list.map((b) => [{ c: 'am', t: '★ ' }, { href: b.url, t: label(b), fav: b.url, title: b.url }]);
async function loadBookmarks() {
  if (!C || !C.bookmarks || !C.bookmarks.getRecent) return;
  const list = await guard(() => C.bookmarks.getRecent(8));
  const ok = (list || []).filter((b) => b.url);
  if (ok.length) setData(pBm, ok, String(ok.length));
}

/* ------------------------------------------------------------ data: downloads */
pDl.rowsFn = (inner, list) =>
  list.map((d) => {
    const name = (d.filename || d.url || '').split(/[\\/]/).pop() || '(unnamed)';
    const st = d.state === 'complete' ? 'on' : d.state === 'in_progress' ? 'warn' : 'off';
    const size = d.state === 'in_progress' && d.totalBytes > 0 ? Math.round((d.bytesReceived / d.totalBytes) * 100) + '%' : bytes(d.fileSize > 0 ? d.fileSize : d.totalBytes);
    return [{ c: st === 'warn' ? 'am' : st === 'on' ? 'on' : 'dim', t: A.lamp(st) + ' ' }, { fill: true, c: 'ice', t: name }, { c: 'dim', t: ' ' + size }];
  });
async function loadDownloads() {
  if (!C || !C.downloads) return;
  const list = await guard(() => C.downloads.search({ orderBy: ['-startTime'], limit: 6 }));
  if (list && list.length) setData(pDl, list, String(list.length));
}

/* ------------------------------------------------------------ data: cpu */
const cpuHist = [];
let cpuPrev = null;
let cpuInfo = null;
pCore.rowsFn = (inner, d) => {
  const n = d.hist.length;
  const two = inner >= 38 && n > 1;
  const colW = two ? Math.floor((inner - 2) / 2) : inner;
  const sw = Math.max(6, colW - 10);
  const cell = (i) => {
    const h = d.hist[i];
    const u = h[h.length - 1] ?? 0;
    const cls = u > 85 ? 'am' : 'on';
    return [{ c: 'dim', t: 'C' + pad2(i) + ' ' }, { c: cls, t: A.sparkline(h, sw) }, { c: u > 85 ? 'am' : 'ice', t: A.fit(Math.round(u) + '%', 5, 'r') }];
  };
  const rows = [];
  if (d.model) rows.push([{ c: 'ice', t: d.model, fill: true }]);
  const avg = d.hist.reduce((a, h) => a + (h[h.length - 1] ?? 0), 0) / n;
  rows.push([{ c: 'dim', t: 'ALL ' }, { c: avg > 85 ? 'am' : 'on', t: A.sparkline(d.avg, inner - 10) }, { c: 'ice', t: A.fit(Math.round(avg) + '%', 6, 'r') }]);
  for (let i = 0; i < n; i += two ? 2 : 1) {
    const r = cell(i);
    if (two && i + 1 < n) r.push({ t: '  ' }, ...cell(i + 1));
    rows.push(r);
  }
  return rows;
};
async function sampleCpu() {
  if (!C || !C.system || !C.system.cpu || document.hidden) return;
  const info = await guard(() => C.system.cpu.getInfo());
  if (!info || !info.processors) return;
  const procs = info.processors.map((p) => p.usage);
  if (cpuPrev && cpuPrev.length === procs.length) {
    const us = procs.map((u, i) => {
      const dT = u.total - cpuPrev[i].total;
      const dI = u.idle - cpuPrev[i].idle;
      return dT > 0 ? A.clamp(1 - dI / dT) * 100 : 0;
    });
    us.forEach((u, i) => {
      (cpuHist[i] = cpuHist[i] || []).push(u);
      if (cpuHist[i].length > 48) cpuHist[i].shift();
    });
    const avgNow = us.reduce((a, b) => a + b, 0) / us.length;
    cpuAvg.push(avgNow);
    if (cpuAvg.length > 48) cpuAvg.shift();
    cpuInfo = info;
    setData(pCore, { hist: cpuHist, avg: cpuAvg, model: info.modelName }, info.numOfProcessors + ' thr');
  }
  cpuPrev = procs;
}
const cpuAvg = [];

/* ------------------------------------------------------------ data: memory + storage */
pMem.rowsFn = (inner, d) => {
  const rows = [];
  if (d.mem) {
    const used = d.mem.capacity - d.mem.availableCapacity;
    const pct = (used / d.mem.capacity) * 100;
    const bw = Math.max(6, inner - 24);
    rows.push([{ c: 'dim', t: 'RAM ' }, { bar: A.barParts(pct, bw, { fine: true }), c: pct > 85 ? 'am' : 'on' }, { c: 'ice', t: ' ' + A.fit(Math.round(pct) + '%', 4, 'r') }]);
    rows.push([{ c: 'dim', t: `    ${bytes(used)} / ${bytes(d.mem.capacity)}` }]);
  }
  for (const s of d.disks || []) {
    rows.push([{ c: 'dim', t: A.lamp('on') + ' ' }, { fill: true, c: 'ice', t: s.name || s.id || 'disk' }, { c: 'dim', t: ' ' + (s.type || '') + ' ' }, { c: 'am', t: bytes(s.capacity) }]);
  }
  return rows;
};
async function loadMem() {
  const mem = C && C.system && C.system.memory ? await guard(() => C.system.memory.getInfo()) : undefined;
  const disks = C && C.system && C.system.storage ? await guard(() => C.system.storage.getInfo()) : undefined;
  if (mem || (disks && disks.length)) setData(pMem, { mem, disks: (disks || []).slice(0, 4) });
}

/* ------------------------------------------------------------ data: sys (tabs, windows, uptime) */
let tabInfo = null;
pSys.rowsFn = () => {
  const up = Math.floor((performance.now() - T0) / 1000);
  const upS = `${pad2(Math.floor(up / 3600))}:${pad2(Math.floor(up / 60) % 60)}:${pad2(up % 60)}`;
  const rows = [];
  if (tabInfo) {
    rows.push([{ c: 'dim', t: 'WINDOWS ' }, { c: 'ice', t: A.fit(String(tabInfo.windows), 3, 'r') }, { c: 'dim', t: '   TABS ' }, { c: 'ice', t: A.fit(String(tabInfo.tabs), 4, 'r') }]);
    rows.push([{ c: 'dim', t: 'PINNED  ' }, { c: 'ice', t: A.fit(String(tabInfo.pinned), 3, 'r') }, { c: 'dim', t: '   AUDIO ' }, { c: tabInfo.audible ? 'am' : 'ice', t: A.fit(String(tabInfo.audible), 4, 'r') }]);
  }
  rows.push([{ c: 'dim', t: 'PAGE UP ' }, { c: 'on', t: upS }]);
  return rows;
};
async function loadTabs() {
  if (!C || !C.tabs || !C.tabs.query) return;
  const tabs = await guard(() => C.tabs.query({}));
  if (!tabs) return;
  tabInfo = {
    tabs: tabs.length,
    windows: new Set(tabs.map((t) => t.windowId)).size,
    pinned: tabs.filter((t) => t.pinned).length,
    audible: tabs.filter((t) => t.audible).length,
  };
  setData(pSys, true);
}

/* ------------------------------------------------------------ clock */
function tick() {
  const d = new Date();
  $('#time').textContent = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  $('#date').textContent = d.toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  if (!document.hidden && pSys.shown) drawPanel(pSys);
}

/* ------------------------------------------------------------ background: reactor + wipe-in */
const bgC = $('#bg');
const bgx = bgC.getContext('2d');
const frameState = { build: 0, t: 0, boost: 0, speed: 1, shock: null };
let dust = [];
let tt = 0;
let lastNow = 0;
let startAt = performance.now();
let cx = 0;
let cy = 0;
let R = 300;
function resizeBg() {
  W = window.innerWidth;
  H = window.innerHeight;
  dpr = Math.min(2, window.devicePixelRatio || 1);
  for (const c of [bgC, wipeC]) {
    c.width = Math.round(W * dpr);
    c.height = Math.round(H * dpr);
    c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  gcw = A.clamp(Math.round(Math.min(W, H) / 120), 6, 9);
  gch = Math.round(gcw * 1.65);
  atlas = A.glyphAtlas(gcw, gch, dpr, ACCENT);
  const narrow = W <= 960;
  cx = W / 2;
  cy = narrow ? Math.min(H * 0.4, 360) : H * 0.5;
  R = Math.min(W * 0.46, narrow ? 300 : H * 0.4, 440);
  dust = [];
  const cols = Math.ceil(W / gcw);
  const rows = Math.ceil(H / gch);
  for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) if (A.hash(x * 1.7, y * 2.3) > 0.992) dust.push([x, y, A.hash(y, x) * 6]);
  paintBg(performance.now());
}
function paintBg(now) {
  if (!atlas) return;
  const dt = lastNow ? Math.min(0.1, (now - lastNow) / 1000) : 0;
  lastNow = now;
  const t = reduced ? 3 : (now - startAt) / 1000;
  const wipeP = reduced ? 1 : A.clamp(t / 1.0);
  const spin = reduced ? 0 : 0.28 + 0.72 * A.sstep(0, 1.6, t);
  tt += dt * spin * 0.8;
  frameState.build = reduced ? 3 : t * 1.9;
  frameState.t = reduced ? 3 : tt + 1;
  frameState.speed = 1;
  frameState.boost = reduced ? 0 : Math.max(0, 0.9 * Math.exp(-(t - 1.15) * 3)) * (t > 1.1 ? 1 : 0);
  frameState.shock = !reduced && t > 1.1 && t < 2 ? [0.3 + (t - 1.1) * 1.5, (1 - (t - 1.1) / 0.9) * 0.8] : null;
  bgx.clearRect(0, 0, W, H);
  const cols = Math.ceil(W / gcw);
  const rows = Math.ceil(H / gch);
  const aa = (0.55 * gch) / R;
  const reach = frameState.shock ? Math.max(1.1, frameState.shock[0] + 0.12) : 1.1;
  const diag = Math.hypot(W, H) / 2;
  const front = wipeP * 1.25;
  const seed = Math.floor(now / 60);
  for (let y = 0; y < rows; y++) {
    const py = (y + 0.5) * gch;
    const ny = (py - cy) / R;
    for (let x = 0; x < cols; x++) {
      const px = (x + 0.5) * gcw;
      if (wipeP < 1) {
        const thr = (Math.hypot(px - W / 2, py - H / 2) / diag) * 0.85 + A.hash(x, y) * 0.3;
        if (thr > front) { // not yet revealed: dim noise
          if (A.hash(x + seed, y) > 0.55) atlas.draw(bgx, A.noiseIndex(A.hash(y, x + seed)), 0.18 * (1 - wipeP) + 0.06, x * gcw, y * gch);
          continue;
        }
        if (thr > front - 0.1) { // wipe edge: bright noise
          atlas.draw(bgx, A.noiseIndex(A.hash(x + seed, y * 3)), 0.55 + 0.45 * A.hash(x, y + seed), x * gcw, y * gch);
          continue;
        }
      }
      if (Math.abs(ny) > reach) continue;
      const nx = (px - cx) / R;
      if (nx * nx + ny * ny > reach * reach) continue;
      const [I, hot] = A.reactorField(nx, ny, frameState, aa);
      if (I < 0.08) continue;
      const Ic = I < 1 ? I : 1;
      const g = hot > 0.9 ? A.RAMP.length - 1 : hot > 0.6 ? A.RAMP.length - 2 : 1 + Math.floor(Ic * (A.RAMP.length - 3.01));
      atlas.draw(bgx, g, Math.min(1, Ic * 0.85 + hot * 0.5), x * gcw, y * gch);
    }
  }
  if (wipeP >= 1) {
    const ph = reduced ? 0 : t;
    for (const [x, y, p] of dust) atlas.draw(bgx, 2, 0.14 + 0.14 * Math.sin(ph * 0.8 + p), x * gcw, y * gch);
  }
}
bgDraw = paintBg;

/* ------------------------------------------------------------ boot */
function layoutAll() {
  measure();
  for (const p of panels) if (p.shown) drawPanel(p);
  layoutPanel(search);
}
let rz = 0;
let lastW = 0;
window.addEventListener('resize', () => {
  clearTimeout(rz);
  rz = setTimeout(() => { resizeBg(); layoutAll(); }, 80);
});
if (window.ResizeObserver) {
  let rt = 0;
  const ro = new ResizeObserver(() => {
    const w = ['#colL', '#colM', '#colR'].map((s) => $(s).clientWidth).join();
    if (w === lastW) return;
    lastW = w;
    clearTimeout(rt);
    rt = setTimeout(layoutAll, 30);
  });
  ['#colL', '#colM', '#colR'].forEach((s) => ro.observe($(s)));
}

async function boot() {
  try { await document.fonts.ready; } catch { /* ignore */ }
  measure();
  resizeBg();
  startAt = performance.now();
  drawPanel(search);
  document.body.classList.remove('boot');
  if (!reduced) {
    decode($('#top'), 700, 150);
    decode($('#hint'), 600, 500);
  }
  tick();
  setInterval(tick, 1000);
  const jobs = [loadSites, loadHistory, loadActivity, loadClosed, loadBookmarks, loadDownloads, loadMem, loadTabs];
  jobs.forEach((j) => guard(j));
  if (C) {
    setInterval(() => { if (!document.hidden) { guard(loadTabs); guard(loadMem); } }, 3000);
    setInterval(sampleCpu, 1000);
    sampleCpu();
  }
  kick();
  input.focus();
  setTimeout(() => input.focus(), 120);
}
boot();
