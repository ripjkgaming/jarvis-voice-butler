(() => {
  'use strict';
  const C = typeof chrome !== 'undefined' && chrome.tabs ? chrome : null;
  const $ = (id) => document.getElementById(id);
  let tabs = [], wins = [], sel = 0, shown = [];

  const host = (u) => { try { return new URL(u).hostname || u; } catch (_) { return u || ''; } };

  function fuzzy(q, s) {
    q = q.toLowerCase(); s = s.toLowerCase();
    if (!q) return 1;
    let i = 0, sc = 0, last = -2;
    for (let j = 0; j < s.length && i < q.length; j++) if (s[j] === q[i]) { sc += j === last + 1 ? 3 : 1; last = j; i++; }
    return i === q.length ? sc + 1 : 0;
  }

  function render() {
    const q = $('q').value.trim();
    const list = $('list'); list.textContent = '';
    shown = [];
    const fr = document.createDocumentFragment();
    wins.forEach((w, wi) => {
      let ts = tabs.filter((t) => t.windowId === w);
      if (q) ts = ts.map((t) => [t, fuzzy(q, (t.title || '') + ' ' + (t.url || ''))]).filter((x) => x[1]).sort((a, b) => b[1] - a[1]).map((x) => x[0]);
      const tot = tabs.filter((t) => t.windowId === w).length;
      if (!ts.length) return;
      const h = document.createElement('div'); h.className = 'win';
      h.innerHTML = `WIN ${String(wi + 1).padStart(2, '0')} <b></b>`;
      h.querySelector('b').textContent = q ? `${ts.length}/${tot}` : `${tot} tabs`;
      fr.appendChild(h);
      ts.forEach((t) => { shown.push(t); fr.appendChild(row(t, shown.length - 1)); });
    });
    if (!shown.length) { const e = document.createElement('div'); e.className = 'empty'; e.textContent = tabs.length ? 'No matching tabs.' : 'No tabs available.'; fr.appendChild(e); }
    list.appendChild(fr);
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    mark();
    $('count').textContent = `${tabs.length} TABS / ${wins.length} WIN`;
  }
  function row(t, idx) {
    const d = document.createElement('div');
    d.className = 'tab' + (t.active ? ' act' : ''); d.dataset.i = idx;
    let fav;
    if (t.favIconUrl && /^(https?|data|chrome-extension):/.test(t.favIconUrl)) {
      fav = document.createElement('img'); fav.className = 'fav'; fav.src = t.favIconUrl;
      fav.onerror = () => { const s = document.createElement('span'); s.className = 'fav none'; s.textContent = '▫'; fav.replaceWith(s); };
    } else { fav = document.createElement('span'); fav.className = 'fav none'; fav.textContent = '▫'; }
    const tt = document.createElement('div'); tt.className = 'tt t';
    tt.textContent = t.title || t.url || '(untitled)';
    const hs = document.createElement('span'); hs.className = 'h'; hs.textContent = host(t.url); tt.appendChild(hs);
    tt.title = (t.title || '') + '\n' + (t.url || '');
    const l = document.createElement('div'); l.className = 'lamps';
    const lamp = (on, ch, amber, tip) => { const i = document.createElement('i'); i.textContent = on ? '●' : '·'; i.title = tip; if (on) i.className = 'on' + (amber ? ' a' : ''); i.dataset.k = ch; return i; };
    l.append(lamp(t.audible, 'A', false, 'audible'), lamp(t.mutedInfo && t.mutedInfo.muted, 'M', true, 'muted'), lamp(t.discarded, 'D', true, 'discarded'), lamp(t.pinned, 'P', false, 'pinned'));
    const x = document.createElement('div'); x.className = 'x'; x.textContent = '✕'; x.title = 'close';
    x.addEventListener('click', (e) => { e.stopPropagation(); close(t); });
    d.append(fav, tt, l, x);
    d.addEventListener('click', () => go(t));
    d.addEventListener('auxclick', (e) => { if (e.button === 1) { e.preventDefault(); close(t); } });
    d.addEventListener('mousedown', (e) => { if (e.button === 1) e.preventDefault(); });
    d.addEventListener('mousemove', () => { if (sel !== idx) { sel = idx; mark(); } });
    return d;
  }
  function mark() {
    document.querySelectorAll('.tab').forEach((e) => {
      const on = +e.dataset.i === sel; e.classList.toggle('sel', on);
      if (on) e.scrollIntoView({ block: 'nearest' });
    });
  }
  async function go(t) {
    if (!C) return;
    try { await C.tabs.update(t.id, { active: true }); await C.windows.update(t.windowId, { focused: true }); window.close(); } catch (_) {}
  }
  async function close(t) {
    if (!C) return;
    try { await C.tabs.remove(t.id); tabs = tabs.filter((x) => x.id !== t.id); wins = wins.filter((w) => tabs.some((x) => x.windowId === w)); render(); } catch (_) {}
  }
  async function load() {
    if (C) tabs = await C.tabs.query({});
    else tabs = [];
    wins = [...new Set(tabs.map((t) => t.windowId))];
    render();
  }

  // key nav
  $('q').addEventListener('input', () => { sel = 0; render(); });
  $('q').addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { sel = Math.min(shown.length - 1, sel + 1); mark(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sel = Math.max(0, sel - 1); mark(); e.preventDefault(); }
    else if (e.key === 'Enter' && shown[sel]) go(shown[sel]);
  });

  $('discard').addEventListener('click', async () => {
    const m = $('discardMsg');
    if (!C) { m.textContent = '// unavailable'; return; }
    const targets = tabs.filter((t) => !t.active && !t.discarded && !t.audible && !t.pinned);
    const r = await Promise.allSettled(targets.map((t) => C.tabs.discard(t.id)));
    const n = r.filter((x) => x.status === 'fulfilled' && x.value).length;
    m.className = 'amber'; m.textContent = `${n} discarded`;
    load();
  });

  load();
  $('q').focus();
})();
