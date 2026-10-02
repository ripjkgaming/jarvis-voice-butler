const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const scope = {
  exports: {},
  require() {
    throw new Error('Unexpected runtime dependency');
  },
  Date,
};
vm.runInNewContext(
  ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/voice-link.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText,
  scope
);
const { voiceLinkFrom } = scope.exports;
const now = 100_000;
const raw = (stage) => ({
  room: 'test',
  waking: true,
  boot: [
    ['wake', 95],
    [stage, 99],
  ],
});

test('summon acknowledges immediately and HTTP acceptance alone is not ready', () => {
  const link = voiceLinkFrom(null, { at: now, baseline: null, failed: false }, now);
  assert.equal(link.phase, 'request');
  assert.equal(link.joining, true);
  assert.equal(link.step, 0);
});
test('actual stages advance without fabricated timed substeps', () => {
  for (const stage of ['wake', 'capture', 'transcribe', 'verify'])
    assert.equal(voiceLinkFrom(raw(stage), null, now).phase, 'request');
  for (const stage of ['check', 'auth', 'connect', 'uplink'])
    assert.equal(voiceLinkFrom(raw(stage), null, now).phase, 'relay');
  assert.equal(voiceLinkFrom(raw('dispatch'), null, now).phase, 'agent');
  assert.equal(voiceLinkFrom(raw('dispatch'), null, 119_000).phase, 'agent');
});
test('online is a short decoration and never delays readiness', () => {
  const link = voiceLinkFrom(raw('online'), null, now);
  assert.equal(link.phase, 'ready');
  assert.equal(link.joining, false);
  assert.equal(voiceLinkFrom(raw('online'), null, 100_101).phase, 'idle');
});
test('a slow join explains the wait without advancing or restarting progress', () => {
  const first = voiceLinkFrom(raw('dispatch'), null, now);
  assert.equal(first.until, 107_000);
  const slow = voiceLinkFrom(raw('dispatch'), null, 107_000);
  assert.equal(slow.phase, 'agent');
  assert.equal(slow.step, first.step);
  assert.match(slow.detail, /STILL WAITING/);
  assert.equal(slow.until, 144_000);
  assert.equal(voiceLinkFrom(raw('dispatch'), null, 144_000).phase, 'stalled');
  assert.equal(voiceLinkFrom(raw('online'), null, 99_001).phase, 'ready');
});
test('relay explanations describe the observed operation', () => {
  assert.match(voiceLinkFrom(raw('auth'), null, now).detail, /AUTHENTICATING/);
  assert.match(voiceLinkFrom(raw('uplink'), null, now).detail, /MICROPHONE UPLINK/);
  assert.match(voiceLinkFrom(raw('connect'), null, 108_000).detail, /STILL CONNECTING/);
});
test('an older room generation cannot acknowledge a new local request', () => {
  const request = { at: now, baseline: 95, failed: false };
  assert.equal(voiceLinkFrom(raw('online'), request, now).phase, 'request');
  assert.equal(
    voiceLinkFrom(
      {
        boot: [
          ['wake', 100],
          ['connect', 100],
        ],
      },
      request,
      now
    ).phase,
    'relay'
  );
});
test('local rejection and missing acknowledgement have truthful distinct states', () => {
  assert.equal(voiceLinkFrom(null, { at: now, baseline: null, failed: true }, now).phase, 'failed');
  const link = voiceLinkFrom(null, { at: now, baseline: null, failed: false }, 110_001);
  assert.equal(link.phase, 'stalled');
  assert.equal(link.joining, false);
});
test('no invented online state on an empty room or expired pending stage', () => {
  assert.equal(voiceLinkFrom({ room: 'x' }, null, now).phase, 'idle');
  assert.equal(voiceLinkFrom(raw('dispatch'), null, 144_001).phase, 'stalled');
  assert.equal(voiceLinkFrom({ room: null, boot: null }, null, now).phase, 'idle');
});
test('invalid timestamps and unrecognised boot stages never create progress', () => {
  assert.equal(voiceLinkFrom({ boot: [['connect', NaN]] }, null, now).phase, 'idle');
  assert.equal(voiceLinkFrom(raw('imaginary-model-ready'), null, now).phase, 'idle');
});

test('legacy boot acknowledgement cannot dismiss a new wake generation', () => {
  let call = {
    boot: [
      ['wake', 10],
      ['online', 11],
    ],
    live: true,
  };
  const state = [],
    effects = [],
    timers = new Map();
  let cursor = 0,
    serial = 0,
    pending = [];
  const react = {
    useState(initial) {
      const i = cursor++;
      if (!(i in state)) state[i] = initial;
      return [
        state[i],
        (value) => {
          state[i] = value;
        },
      ];
    },
    useEffect(fn, deps) {
      const i = cursor++,
        prior = effects[i];
      if (!prior || deps.some((d, n) => !Object.is(d, prior.deps[n])))
        pending.push(() => {
          prior?.cleanup?.();
          effects[i] = { deps, cleanup: fn() };
        });
    },
  };
  const moduleScope = {
    exports: {},
    require(name) {
      if (name === 'react') return react;
      if (name === '@/hooks/hud/use-room-state') return { useCallState: () => call };
      throw new Error(name);
    },
    setTimeout(fn) {
      timers.set(++serial, fn);
      return serial;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  vm.runInNewContext(
    ts.transpileModule(
      fs.readFileSync(path.join(__dirname, '../hooks/hud/use-boot-log.ts'), 'utf8'),
      {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      }
    ).outputText,
    moduleScope
  );
  function render() {
    cursor = 0;
    pending = [];
    const result = moduleScope.exports.useBootLog();
    pending.forEach((fn) => fn());
    return result;
  }
  assert.equal(render().online, true);
  const oldCallback = [...timers.values()][0];
  call = {
    boot: [
      ['wake', 12],
      ['connect', 12.5],
    ],
    live: false,
  };
  assert.equal(render().online, false);
  assert.equal(timers.size, 0, 'Old online timer was not cancelled');
  oldCallback(); // Even an already-queued stale callback can only dismiss its own id.
  assert.equal(render().short, 'Relay');
  call = {
    boot: [
      ['wake', 12],
      ['online', 13],
    ],
    live: true,
  };
  render();
  [...timers.values()][0]();
  assert.equal(render(), null);
});
