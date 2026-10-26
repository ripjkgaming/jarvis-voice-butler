const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function worker() {
  const listeners = {}, timers = new Map(), sent = [];
  let serial = 0, active = { id: 10, windowId: 1, active: true };
  const tabs = {
    query: async () => [active], get: async () => active,
    sendMessage: async (id, message) => sent.push({ id, ...message }),
  };
  for (const name of ['onCreated', 'onRemoved', 'onActivated', 'onUpdated', 'onMoved', 'onZoomChange']) {
    tabs[name] = { addListener: fn => { listeners[name] = fn; } };
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'extension/background.js'), 'utf8'), {
    importScripts: () => {}, nativeCue: () => {},
    chrome: { tabs, runtime: { onInstalled: { addListener: () => {} } } }, setTimeout: fn => { timers.set(++serial, fn); return serial; }, clearTimeout: id => timers.delete(id),
  });
  return { listeners, sent, setActive: tab => { active = tab; }, async flush() {
    const callbacks = [...timers.values()]; timers.clear();
    for (const fn of callbacks) await fn();
  } };
}

test('close survives the accompanying activation and load events', async () => {
  const w = worker();
  w.listeners.onRemoved(9, { windowId: 1, isWindowClosing: false });
  w.listeners.onActivated({ windowId: 1, tabId: 10 });
  w.listeners.onUpdated(10, { status: 'complete' }, { active: true, windowId: 1 });
  await w.flush();
  assert.deepEqual(w.sent, [{ id: 10, jarvisCue: 'close' }]);
});
test('opening then immediately switching sends the cue to the new active tab', async () => {
  const w = worker();
  w.listeners.onCreated({ id: 9, windowId: 1, active: true });
  w.listeners.onActivated({ windowId: 1, tabId: 10 });
  await w.flush();
  assert.deepEqual(w.sent, [{ id: 10, jarvisCue: 'switch' }]);
});
test('background updates and late active-tab events do not affect another page', async () => {
  const w = worker();
  w.listeners.onUpdated(8, { status: 'complete' }, { active: false, windowId: 1 });
  w.listeners.onUpdated(9, { pinned: true }, { active: true, windowId: 1 });
  await w.flush();
  assert.deepEqual(w.sent, []);
});
test('window shutdown cancels its pending cue', async () => {
  const w = worker();
  w.listeners.onActivated({ windowId: 1, tabId: 10 });
  w.listeners.onRemoved(10, { windowId: 1, isWindowClosing: true });
  await w.flush();
  assert.deepEqual(w.sent, []);
});
test('pin cue survives the native tab movement caused by pinning', async () => {
  const w = worker();
  w.listeners.onUpdated(10, { pinned: true }, { active: true, windowId: 1 });
  w.listeners.onMoved(10, { windowId: 1 });
  await w.flush();
  assert.deepEqual(w.sent, [{ id: 10, jarvisCue: 'pin' }]);
});
test('pin, mute, move, zoom and loading events have distinct cues', async () => {
  const w = worker();
  for (const [change, expected] of [[{ pinned: true }, 'pin'], [{ mutedInfo: { muted: true } }, 'mute'], [{ status: 'loading' }, 'loading'], [{ status: 'complete' }, 'ready']]) {
    w.listeners.onUpdated(10, change, { active: true, windowId: 1 });
    await w.flush();
    assert.equal(w.sent.at(-1).jarvisCue, expected);
  }
  w.listeners.onMoved(10, { windowId: 1 }); await w.flush();
  assert.equal(w.sent.at(-1).jarvisCue, 'move');
  await w.listeners.onZoomChange({ tabId: 10 }); await w.flush();
  assert.equal(w.sent.at(-1).jarvisCue, 'zoom');
});

test('focus loss prevents an in-flight native cue from drawing over another app', async () => {
  const posts = [];
  let resolveWindow, ready;
  const event = () => ({ addListener() {} });
  const context = vm.createContext({
    chrome: {
      storage: { local: { get: async () => ({ 'jarvis-quiet': false }) }, onChanged: event() },
      runtime: {
        connectNative: () => ({ postMessage: message => posts.push(message), onMessage: { addListener: fn => { ready = fn; } }, onDisconnect: event() }),
        onMessage: event(),
      },
      windows: { get: () => new Promise(resolve => { resolveWindow = resolve; }), onFocusChanged: event(), onBoundsChanged: event() },
      tabs: { query: async () => [{id: 10}], onRemoved: event() },
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'extension/native.js'), 'utf8'), context);
  await Promise.resolve();
  ready({ready:true});
  const pending = vm.runInContext("nativeCue(1, 'open')", context);
  vm.runInContext('nativeCancel()', context);
  resolveWindow({focused:true,state:'normal',left:0,top:0,width:900,height:700});
  await pending;
  assert.deepEqual(posts.map(message => message.kind), ['cancel']);
});
