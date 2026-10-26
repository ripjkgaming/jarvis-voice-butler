// An optional local helper draws above the native browser tab bar.
// Payloads contain geometry and event types only; no page data or tab titles.
let nativePort, nativeStatus = { ready: false }, nativeQuiet = true, nativeReady = false, nativePending;
let nativeEpoch = 0;
const nativeReduced = new Map();
chrome.storage.local.get('jarvis-quiet').then(settings => {
  nativeQuiet = settings['jarvis-quiet'] === true;
  if (!nativeQuiet) nativeConnect();
});
function nativeConnect() {
  if (nativePort) return nativePort;
  try {
    const port = chrome.runtime.connectNative('org.jarvis.brave_motion');
    nativePort = port;
    port.onMessage.addListener(message => {
      nativeStatus = { ready: nativeStatus.ready, version: nativeStatus.version, ...message };
      if (message.ready) {
        nativeReady = true;
        if (nativePending && Date.now() - nativePending.at < 1500) port.postMessage(nativePending.payload);
        nativePending = undefined;
      }
    });
    port.onDisconnect.addListener(() => {
      const error = chrome.runtime.lastError?.message;
      if (nativePort === port) nativePort = undefined;
      nativeReady = false;
      nativeStatus = { ready: false, error };
    });
    return port;
  } catch (_) { return null; }
}
function nativeCancel() {
  nativeEpoch++;
  nativePending = undefined;
  try { nativePort?.postMessage({ kind: 'cancel' }); } catch (_) { /* Disconnected. */ }
}
async function nativeCue(windowId, kind) {
  if (nativeQuiet) return;
  const epoch = nativeEpoch;
  try {
    const window = await chrome.windows.get(windowId);
    if (!window.focused || ['minimized', 'fullscreen'].includes(window.state)) return;
    const [active] = await chrome.tabs.query({ windowId, active: true });
    if (nativeQuiet || epoch !== nativeEpoch) return;
    if (nativeReduced.get(active?.id)) { nativeCancel(); return; }
    const payload = { kind, window: {
      focused: window.focused, state: window.state,
      left: window.left, top: window.top, width: window.width, height: window.height,
    } };
    const port = nativeConnect();
    if (port && nativeReady) port.postMessage(payload);
    else nativePending = { at: Date.now(), payload };
  } catch (_) { /* Closing windows and missing hosts are ordinary fallbacks. */ }
}
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes['jarvis-quiet']) {
    nativeQuiet = changes['jarvis-quiet'].newValue === true;
    if (nativeQuiet) nativeCancel();
  }
});
chrome.windows.onFocusChanged.addListener(nativeCancel);
chrome.windows.onBoundsChanged.addListener(nativeCancel);
chrome.tabs.onRemoved.addListener(id => nativeReduced.delete(id));
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id === chrome.runtime.id && message.jarvisNativeStatus) reply(nativeStatus);
  if (sender.id === chrome.runtime.id && sender.tab && typeof message.jarvisMotionReduced === 'boolean') {
    nativeReduced.set(sender.tab.id, message.jarvisMotionReduced);
    if (sender.tab.active && message.jarvisMotionReduced) nativeCancel();
  }
});
