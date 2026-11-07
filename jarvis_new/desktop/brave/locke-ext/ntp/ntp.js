const C = globalThis.chrome;
const status = document.getElementById('status');
const safeURL = value => {
  try { const u = new URL(value); return ['http:', 'https:', 'file:', 'ftp:'].includes(u.protocol) ? u.href : null; }
  catch { return null; }
};
function list(id, items) {
  const root = document.getElementById(id);
  root.replaceChildren();
  for (const item of items) {
    const url = safeURL(item.url);
    if (!url) continue;
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = url;
    a.textContent = item.title || url;
    li.append(a); root.append(li);
  }
  if (!root.children.length) {
    const li = document.createElement('li'); li.className = 'empty'; li.textContent = 'Nothing here yet.'; root.append(li);
  }
}
async function load(id, read) {
  try { list(id, await read()); } catch { list(id, []); }
}
document.getElementById('search').addEventListener('submit', e => {
  e.preventDefault();
  const text = document.getElementById('query').value.trim();
  if (!text) return;
  let url = safeURL(text);
  if (!url && !/\s/.test(text) && /^(localhost|[^/]+\.[^/]+)(:\d+)?(\/|$)/.test(text)) url = safeURL('https://' + text);
  location.assign(url || 'https://search.brave.com/search?q=' + encodeURIComponent(text));
});
load('sites', async () => (await C.topSites.get()).slice(0, 12));
load('bookmarks', async () => (await C.bookmarks.getRecent(12)));
load('recent', async () => (await C.sessions.getRecentlyClosed({ maxResults: 12 })).flatMap(s => s.tab ? [s.tab] : s.window?.tabs || []));
// Keyboard shortcut focuses the search input without any visual effect.
document.addEventListener('keydown', e => {
  if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
    e.preventDefault(); document.getElementById('query').focus();
  }
});
