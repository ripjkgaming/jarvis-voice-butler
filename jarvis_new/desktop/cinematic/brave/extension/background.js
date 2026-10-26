// Coalesce overlapping browser events. No URLs, titles or history are stored.
importScripts('native.js');
const pending = new Map();
const priority = { switch: 1, loading: 2, ready: 2, move: 3, zoom: 3, pin: 4, mute: 4, open: 5, close: 6 };

function cue(windowId, kind, tabId) {
  const previous = pending.get(windowId);
  if (previous && (previous.tabId == null || previous.tabId === tabId) && priority[previous.kind] > priority[kind]) return;
  clearTimeout(previous?.timer);
  const event = { kind, tabId };
  event.timer = setTimeout(async () => {
    pending.delete(windowId);
    try {
      const [tab] = await chrome.tabs.query({ windowId, active: true });
      // A late update from a background tab must never animate the active page.
      if (tab?.id == null || (event.tabId != null && tab.id !== event.tabId)) return;
      nativeCue(windowId, event.kind);
      await chrome.tabs.sendMessage(tab.id, { jarvisCue: event.kind });
    } catch (_) { /* Internal pages and unloaded documents have no receiver. */ }
  }, 70);
  pending.set(windowId, event);
}

chrome.tabs.onCreated.addListener(tab => {
  if (tab.active) cue(tab.windowId, 'open', tab.id);
  else nativeCue(tab.windowId, 'open');
});
chrome.tabs.onRemoved.addListener((id, info) => {
  if (!info.isWindowClosing) cue(info.windowId, 'close');
  else {
    clearTimeout(pending.get(info.windowId)?.timer);
    pending.delete(info.windowId);
  }
});
chrome.tabs.onActivated.addListener(info => cue(info.windowId, 'switch', info.tabId));
chrome.tabs.onUpdated.addListener((id, change, tab) => {
  if (!tab.active) {
    if ('pinned' in change) nativeCue(tab.windowId, 'pin');
    else if ('mutedInfo' in change) nativeCue(tab.windowId, 'mute');
    return;
  }
  if ('pinned' in change) cue(tab.windowId, 'pin', id);
  else if ('mutedInfo' in change) cue(tab.windowId, 'mute', id);
  else if (change.status) cue(tab.windowId, change.status === 'loading' ? 'loading' : 'ready', id);
});
chrome.tabs.onMoved.addListener((id, info) => cue(info.windowId, 'move', id));
chrome.tabs.onZoomChange.addListener(async info => {
  try {
    const tab = await chrome.tabs.get(info.tabId);
    if (tab.active) cue(tab.windowId, 'zoom', tab.id);
  } catch (_) { /* The tab may already be closed. */ }
});

// Upgrade already-open pages without reloading them or losing form state.
chrome.runtime.onInstalled.addListener(async () => {
  const tabs = await chrome.tabs.query({ url: ['http://*/*', 'https://*/*'] });
  await Promise.allSettled(tabs.filter(tab => !tab.discarded).map(tab =>
    chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['transition.js'] })
  ));
});
