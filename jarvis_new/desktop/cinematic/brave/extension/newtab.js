const $ = id => document.getElementById(id);
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const api = globalThis.chrome?.tabs;
const closing = new Set();
let quiet = true, windowId, ownId, generation = 0, initialized = false;
const moving = new Map();
const canMove = () => !quiet && !reduced.matches && !document.hidden;

function motion() {
  document.body.classList.toggle('quiet', quiet);
  $('motion').textContent = quiet ? 'Motion off' : reduced.matches ? 'Reduced motion' : 'Motion on';
  $('motion').setAttribute('aria-pressed', String(quiet));
  $('motion').title = 'Toggle animations across Jarvis tabs and webpages';
  if (quiet || reduced.matches) document.getAnimations().forEach(animation => animation.cancel());
}
function report(message) { $('status').textContent = message; }
$('motion').onclick = async () => {
  try {
    const next = !quiet;
    await chrome.storage.local.set({ 'jarvis-quiet': next });
    quiet = next;
    motion();
  } catch (_) { report('Could not save the motion setting. Try again.'); }
};
reduced.addEventListener('change', motion);
if (globalThis.chrome?.storage) {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes['jarvis-quiet']) {
      quiet = changes['jarvis-quiet'].newValue === true;
      motion();
    }
  });
}

function clock() {
  const now = new Date();
  $('clock').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  $('date').textContent = now.toLocaleDateString([], { weekday: 'short', month: 'short', day: '2-digit' }).toUpperCase();
  $('zone').textContent = Intl.DateTimeFormat().resolvedOptions().timeZone.replaceAll('_', ' ').toUpperCase();
}
clock();
setInterval(clock, 30000);
$('search').onsubmit = event => {
  event.preventDefault();
  const value = $('query').value.trim();
  if (!value) return;
  let url;
  if (/^https?:\/\//i.test(value)) url = value;
  else if (/^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/:?#].*)?$/i.test(value)) url = 'https://' + value;
  else url = 'https://search.brave.com/search?q=' + encodeURIComponent(value);
  location.href = url;
};
$('new-tab').onclick = async () => {
  try { await api.create({ windowId }); }
  catch (_) { report('Could not open a tab. Try again.'); }
};

function animate(element, frames, duration = 420) {
  if (!canMove()) return Promise.resolve();
  return element.animate(frames, { duration, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'none' }).finished.catch(() => {});
}
function pulse(element) {
  return animate(element, [
    { boxShadow: 'inset 0 0 0 1px #96d4ff00' },
    { boxShadow: 'inset 0 0 0 1px #96d4ff99', offset: .3 },
    { boxShadow: 'inset 0 0 0 1px #96d4ff00' },
  ]);
}
async function dissolve(row) {
  row.classList.add('closing');
  await animate(row, [
    { opacity: 1, transform: 'translateX(0)', clipPath: 'inset(0)' },
    { opacity: .8, transform: 'translateX(4px)', clipPath: 'inset(0)', offset: .3 },
    { opacity: 0, transform: 'translateX(14px) scale(.96,.6)', clipPath: 'inset(45% 0)' },
  ], 600);
  row.classList.add('dissolved');
}
function positions() {
  return new Map([...$('tabs').children].map(row => [row, row.getBoundingClientRect().top]));
}
function reflow(before) {
  for (const row of $('tabs').children) {
    const dy = before.get(row) - row.getBoundingClientRect().top;
    if (!dy || row.classList.contains('closing') || !canMove()) continue;
    moving.get(row)?.cancel();
    const animation = row.animate([{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }], {
      duration: 340, easing: 'cubic-bezier(.16,1,.3,1)',
    });
    moving.set(row, animation);
    animation.finished.catch(() => {}).finally(() => { if (moving.get(row) === animation) moving.delete(row); });
  }
}
function removeRow(row) {
  const before = positions();
  if (row.contains(document.activeElement) || (row.hadFocus && document.activeElement === document.body)) {
    const next = row.nextElementSibling || row.previousElementSibling;
    (next?.querySelector('.tab-link') || $('new-tab')).focus();
  }
  row.remove();
  reflow(before);
}
function actionButton(label, className, glyph, handler) {
  const button = document.createElement('button');
  button.className = className;
  button.textContent = glyph;
  button.title = label;
  button.setAttribute('aria-label', label);
  button.onclick = async () => {
    if (button.disabled) return;
    button.disabled = true;
    try { await handler(); }
    catch (_) { report('That tab is no longer available, or the action could not finish.'); }
    finally { button.disabled = false; refresh(); }
  };
  return button;
}
function makeRow(tab) {
  const row = document.createElement('div');
  row.className = 'tab';
  row.dataset.id = tab.id;
  row.setAttribute('role', 'listitem');
  const link = actionButton('Switch to tab', 'tab-link', '', async () => {
    await animate(row, [{ transform: 'translateX(0)', opacity: 1 }, { transform: 'translateX(8px)', opacity: .7 }], 140);
    await api.update(tab.id, { active: true });
  });
  const number = document.createElement('span'); number.className = 'tab-number';
  const label = document.createElement('span'); label.className = 'tab-label';
  link.append(number, label);
  const pin = actionButton('Pin tab', 'tab-action pin', '⌖', () => api.update(tab.id, { pinned: !row.tab.pinned }));
  const mute = actionButton('Mute tab', 'tab-action mute', '♪', () => api.update(tab.id, { muted: !row.tab.mutedInfo?.muted }));
  const reload = actionButton('Reload tab', 'tab-action reload', '↻', async () => {
    pulse(row);
    await api.reload(tab.id);
  });
  const close = actionButton('Close tab', 'close', '×', async () => {
    if (closing.has(tab.id)) return;
    closing.add(tab.id);
    row.hadFocus = row.contains(document.activeElement);
    row.querySelectorAll('button').forEach(button => { button.disabled = true; });
    try {
      await dissolve(row);
      await api.remove(tab.id);
      removeRow(row);
    } finally {
      closing.delete(tab.id);
      row.classList.remove('closing', 'dissolved');
      row.querySelectorAll('button').forEach(button => { button.disabled = false; });
      if (row.isConnected && row.hadFocus && document.activeElement === document.body) close.focus();
      row.hadFocus = false;
    }
  });
  row.append(link, pin, mute, reload, close);
  return row;
}
async function refresh() {
  if (!api || windowId == null) return;
  const request = ++generation;
  try {
    const tabs = (await api.query({ windowId })).filter(tab => tab.id !== ownId).sort((a, b) => a.index - b.index);
    if (request !== generation) return;
    const before = positions();
    const current = new Set(tabs.map(tab => String(tab.id)));
    $('count').textContent = String(tabs.length).padStart(2, '0');
    $('empty').hidden = tabs.length > 0;
    for (const row of [...$('tabs').children]) {
      if (current.has(row.dataset.id) || row.dataset.removing) continue;
      row.dataset.removing = 'true';
      // Native closes already happened; only our own row can dissolve afterward.
      if (closing.has(Number(row.dataset.id))) removeRow(row);
      else dissolve(row).then(() => removeRow(row));
    }
    tabs.forEach((tab, index) => {
      let row = $('tabs').querySelector(`[data-id="${tab.id}"]`);
      const fresh = !row;
      if (!row) row = makeRow(tab);
      const previous = row.tab;
      row.tab = tab;
      row.querySelector('.tab-number').textContent = String(index + 1).padStart(2, '0');
      row.querySelector('.tab-label').textContent = tab.title || 'Untitled tab';
      row.querySelector('.tab-link').title = tab.title || 'Untitled tab';
      for (const [name, active, text] of [
        ['pin', tab.pinned, tab.pinned ? 'Unpin' : 'Pin'],
        ['mute', tab.mutedInfo?.muted, tab.mutedInfo?.muted ? 'Unmute' : 'Mute'],
      ]) {
        const button = row.querySelector('.' + name);
        button.setAttribute('aria-pressed', String(Boolean(active)));
        button.title = text + ' tab';
        button.setAttribute('aria-label', text + ' ' + (tab.title || 'tab'));
      }
      row.querySelector('.close').setAttribute('aria-label', 'Close ' + (tab.title || 'tab'));
      row.querySelector('.reload').setAttribute('aria-label', 'Reload ' + (tab.title || 'tab'));
      row.classList.toggle('pinned', Boolean(tab.pinned));
      row.classList.toggle('loading', tab.status === 'loading');
      row.classList.toggle('audible', Boolean(tab.audible && !tab.mutedInfo?.muted));
      const slot = $('tabs').children[index];
      if (slot !== row) $('tabs').insertBefore(row, slot || null);
      if (fresh) {
        animate(row, [{ opacity: 0, transform: 'translateX(18px)', clipPath: 'inset(0 0 0 100%)' }, { opacity: 1, transform: 'none', clipPath: 'inset(0)' }], 620);
      } else if (!closing.has(tab.id) && previous && (previous.pinned !== tab.pinned || previous.mutedInfo?.muted !== tab.mutedInfo?.muted || (previous.status === 'loading' && tab.status === 'complete'))) pulse(row);
    });
    reflow(before);
  } catch (_) { report('Could not refresh tabs. Try opening a new tab.'); }
}

async function start() {
  motion();
  if (!api) {
    $('empty').textContent = 'Your tabs will appear when installed in Brave.';
    $('new-tab').disabled = $('motion').disabled = true;
    return;
  }
  try {
    const settings = await chrome.storage.local.get('jarvis-quiet');
    quiet = settings['jarvis-quiet'] ?? (localStorage.getItem('jarvis-quiet') === '1');
    if (settings['jarvis-quiet'] === undefined) await chrome.storage.local.set({ 'jarvis-quiet': quiet });
    motion();
    document.body.classList.add('motion-ready');
    const own = await api.getCurrent();
    ownId = own.id;
    windowId = own.windowId;
    initialized = true;
    await refresh();
  } catch (_) { report('Tab controls could not initialize. Reload this page to retry.'); }
}
if (api) {
  api.onCreated.addListener(tab => { if (tab.windowId === windowId) refresh(); });
  api.onRemoved.addListener((id, info) => { if (info.windowId === windowId) refresh(); });
  api.onUpdated.addListener((id, change, tab) => { if (tab.windowId === windowId) refresh(); });
  api.onMoved.addListener((id, info) => { if (info.windowId === windowId) refresh(); });
  api.onAttached.addListener((id, info) => {
    if (id === ownId) windowId = info.newWindowId;
    if (info.newWindowId === windowId) refresh();
  });
  api.onDetached.addListener((id, info) => { if (info.oldWindowId === windowId) refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && initialized) refresh(); });
}
start();
