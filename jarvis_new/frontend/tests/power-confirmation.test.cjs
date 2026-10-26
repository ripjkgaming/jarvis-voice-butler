const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
const scope = {exports: {}, performance};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/power-confirmation.ts'), 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, scope);
const {PowerConfirmation} = scope.exports;

test('first real click requests, second confirms same action exactly once', async () => {
  const calls = [];
  const control = new PowerConfirmation(async body => {
    calls.push(JSON.parse(JSON.stringify(body)));
    return {confirmation_required: true, confirmation: 'ticket'};
  }, () => {}, () => 0);
  assert.equal(await control.click('shutdown', true, false), false);
  assert.equal(calls.length, 0);
  assert.equal(await control.click('shutdown', true, true), false);
  assert.equal(await control.click('shutdown', true, true), true);
  assert.deepEqual(calls, [{action: 'shutdown', phase: 'request'}, {action: 'shutdown', phase: 'confirm', confirmation: 'ticket'}]);
  await control.click('shutdown', true, true);
  assert.equal(calls[2].phase, 'request');
});

test('expiry and changed action require a fresh first click', async () => {
  let now = 0;
  const calls = [];
  const control = new PowerConfirmation(async body => {
    calls.push(body);return {confirmation_required: true, confirmation: 'ticket'};
  }, () => {}, () => now);
  await control.click('shutdown', true, true);
  now = 3500;
  await control.click('shutdown', true, true);
  await control.click('restart', true, true);
  assert.ok(calls.every(body => body.phase === 'request'));
});

test('pending or unmounted request cannot publish a ticket', async () => {
  let finish;
  const calls = [];
  const control = new PowerConfirmation(body => {
    calls.push(body);return new Promise(resolve => {finish = resolve;});
  }, () => {});
  const request = control.click('restart', true, true);
  assert.equal(await control.click('restart', true, true), false);
  assert.equal(calls.length, 1);
  control.dispose();finish({confirmation_required: true, confirmation: 'ticket'});
  assert.equal(await request, false);
  assert.equal(await control.click('restart', true, true), false);
});

test('failed confirmation never retains ticket; lock remains one real click', async () => {
  const calls = [];
  const control = new PowerConfirmation(async body => {
    calls.push(body);
    if (body.phase === 'confirm') throw Error('offline');
    return {confirmation_required: true, confirmation: 'ticket'};
  }, () => {});
  await control.click('shutdown', true, true);
  assert.equal(await control.click('shutdown', true, true), false);
  await control.click('shutdown', true, true);
  assert.equal(calls[2].phase, 'request');
  assert.equal(await control.click('lock', false, true), true);
  assert.equal(calls[3].action, 'lock');assert.equal(calls[3].phase, undefined);
});

test('actual PowerRow forwards trusted clicks and keeps its existing two-click gesture', async () => {
  const calls = [], cleanups = [];
  const fakeReact = {
    memo: value => value,
    useState: () => [null, () => {}],
    useRef: () => ({current: null}),
    useEffect: fn => { const cleanup = fn(); if (cleanup) cleanups.push(cleanup); },
    useMemo: fn => fn(),
  };
  const jsx = (type, props) => ({type, props});
  const dependencies = {
    react: fakeReact,
    'react/jsx-runtime': {jsx, jsxs: jsx},
    '@/components/hud/ambient-particles': {AmbientParticles: () => null},
    '@/lib/power-confirmation': {PowerConfirmation},
    '@/lib/bridge': {bridgePost: async (route, body) => {
      assert.equal(route, '/power');calls.push(JSON.parse(JSON.stringify(body)));
      return {confirmation_required: true, confirmation: 'ticket'};
    }},
  };
  const component = {exports: {}, require: name => {
    assert.ok(name in dependencies, name);return dependencies[name];
  }};
  const source = fs.readFileSync(path.join(__dirname, '../components/hud/school-menus.tsx'), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText + '\nexports.PowerRow = PowerRow;', component);
  let closed = 0;
  const row = component.exports.PowerRow({onDone: () => closed++});
  const shutdown = row.props.children.find(button => button.props['data-danger'] && button.props.children[1].props.children === 'Shut down');
  const flush = async () => {for(let i=0;i<8;i++) await Promise.resolve();};
  shutdown.props.onClick({isTrusted: false});await flush();assert.equal(calls.length, 0);
  shutdown.props.onClick({isTrusted: true});await flush();assert.equal(calls.length, 1);assert.equal(closed, 0);
  shutdown.props.onClick({isTrusted: true});await flush();assert.equal(calls.length, 2);assert.equal(closed, 1);
  assert.equal(calls[0].phase, 'request');assert.equal(calls[1].phase, 'confirm');
  cleanups.forEach(cleanup => cleanup());
});
