/* eslint-disable @typescript-eslint/no-require-imports, react-hooks/rules-of-hooks */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
let call,
  room,
  agent,
  desktop = true;
function load(file, mocks) {
  const scope = {
    exports: {},
    process: { env: {} },
    require: (name) => {
      if (name in mocks) return mocks[name];
      throw Error(name);
    },
  };
  vm.runInNewContext(
    ts.transpileModule(fs.readFileSync(path.join(__dirname, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText,
    scope
  );
  return scope.exports;
}
const { callStateFrom } = load('../lib/bridge.ts', { '@/lib/tauri': {} });
const palette = load('../lib/jarvis-palette.ts', {});
const { useJarvisState, JARVIS_COLORS } = load('../hooks/hud/use-jarvis-state.ts', {
  react: { useMemo: (fn) => fn() },
  '@livekit/components-react': { useAgent: () => ({ state: agent }) },
  '@/hooks/hud/use-room-state': { useCallState: () => call, useRoomState: () => room },
  '@/lib/tauri': { isTauri: () => desktop },
  '@/lib/jarvis-palette': palette,
});
function check(raw, expected, sdk = 'disconnected') {
  room = raw;
  call = callStateFrom(raw);
  agent = sdk;
  assert.equal(useJarvisState().jarvis, expected);
  assert.equal(useJarvisState().color, JARVIS_COLORS[expected]);
}
test('leased call lifecycle: quiet, speech, execution, interruption, error, close, reconnect', () => {
  desktop = true;
  const live = (state) => ({ room: 'desktop', call_live: true, agent_state: state });
  for (const [raw, expected] of [
    [null, 'idle'],
    [{ room: 'old' }, 'idle'],
    [{ room: 'old', call_live: false, agent_state: 'speaking' }, 'idle'],
    [{ room: 'a', call_live: false, waking: true, boot: [['dispatch', 1]] }, 'idle'],
    [live('listening'), 'listening'],
    [live('speaking'), 'speaking'],
    [live('thinking'), 'thinking'],
    [live('thinking'), 'thinking'],
    [live('listening'), 'listening'], // tool error / waiting
    [live('speaking'), 'speaking'],
    [live('listening'), 'listening'], // interruption
    [live(null), 'listening'], // activity missing but separately confirmed active
    [{ room: 'a', call_live: false }, 'idle'],
    [null, 'idle'], // lease expires / bridge stall
    [{ room: 'b', call_live: true, agent_state: 'speaking', boot: [['online', 2]] }, 'speaking'],
  ])
    check(raw, expected);
});
test('a fresh session lease outranks incomplete or stale wake progress', () => {
  desktop = true;
  for (const stage of ['dispatch', 'connect', 'agent-wait', 'error']) {
    for (const state of ['listening', 'speaking', 'thinking']) {
      check(
        { room: 'a', call_live: true, agent_state: state, waking: true, boot: [[stage, 1]] },
        state
      );
    }
  }
  check({ room: '', call_live: true, agent_state: 'speaking' }, 'idle');
  check({ room: 'a', call_live: false, boot: [['online', 1]] }, 'idle');
});
test('desktop bridge is authoritative over a conflicting SDK context', () => {
  desktop = true;
  check({ room: 'a', call_live: false }, 'idle', 'speaking');
  check({ room: 'a', call_live: true, agent_state: 'thinking' }, 'thinking', 'speaking');
});
test('browser SDK quiet call and speech share purple, thinking green', () => {
  desktop = false;
  for (const state of ['listening', 'speaking', 'thinking']) check(null, state, state);
  check(null, 'idle', 'failed');
  desktop = true;
  assert.equal(JARVIS_COLORS.listening, '#a855f7');
  assert.equal(JARVIS_COLORS.speaking, '#a855f7');
  assert.equal(JARVIS_COLORS.thinking, '#22c55e');
  assert.equal(JARVIS_COLORS.idle, '#22d3ee');
});
