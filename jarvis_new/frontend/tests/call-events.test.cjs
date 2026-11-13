// Run from frontend: node --test tests/call-events.test.cjs
// Calls HUD reducer over the contract event stream (docs/calls/CONTRACT.md).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../components/calls/call-reducer.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const scope = { exports: {}, require: () => ({}), Math, Number, String };
vm.runInNewContext(compiled, scope, { filename: 'call-reducer.ts' });
const { parseCallEvent, reduceCall, toMs } = scope.exports;

const caller = {
  name: 'Priya',
  number: '+447700900123',
  trust: 'work',
  relationship: 'x',
  notes: '',
};
const state = (st, extra = {}) => ({
  type: 'call',
  event: 'state',
  call_id: 'c1',
  state: st,
  direction: 'in',
  caller,
  goal: 'g',
  started_at: 1000,
  mode: 'jarvis',
  ai_disclosed: true,
  recording: true,
  ...extra,
});
const line = (speaker, text, partial) => ({
  type: 'call',
  event: 'transcript',
  call_id: 'c1',
  speaker,
  text,
  partial,
  ts: 1001,
});
const fold = (evs, t = 2_000_000) =>
  evs.reduce((v, e) => reduceCall(v, parseCallEvent(e), t), null);
const speech = (v) => v.transcript.filter((l) => !l.mark);

test('ignores non-call payloads and unknown states', () => {
  assert.equal(parseCallEvent({ kind: 'tool_start' }), null);
  assert.equal(
    parseCallEvent({ type: 'call', event: 'state', call_id: 'c', state: 'bogus' }),
    null
  );
  assert.equal(parseCallEvent(null), null);
});

test('epoch seconds become ms', () => {
  assert.equal(toMs(1000, 5), 1_000_000);
  assert.equal(toMs(1_700_000_000_000, 5), 1_700_000_000_000);
  assert.equal(toMs(undefined, 5), 5);
});

test('partials stream into one line per speaker, final replaces it', () => {
  const v = fold([
    state('handling'),
    line('caller', 'Hi', true),
    line('jarvis', 'Hello', true),
    line('caller', 'Hi Jarvis', true),
    line('caller', 'Hi Jarvis, is he in?', false),
  ]);
  const s = speech(v);
  assert.equal(s.length, 2);
  assert.equal(s[0].text, 'Hi Jarvis, is he in?');
  assert.equal(s[0].partial, false);
  assert.equal(s[1].partial, true);
});

test('escalation shows until the state changes', () => {
  let v = fold([
    state('handling'),
    { type: 'call', event: 'escalation', call_id: 'c1', reason: 'pay' },
  ]);
  assert.equal(v.escalation.reason, 'pay');
  v = reduceCall(v, parseCallEvent(state('handling')), 3);
  assert.equal(v.escalation.reason, 'pay', 'same-state refresh keeps it');
  v = reduceCall(v, parseCallEvent(state('owner')), 4);
  assert.equal(v.escalation, null);
});

test('state changes write markers; ended freezes the timer and keeps the summary', () => {
  const v = fold([
    state('ringing'),
    state('handling'),
    state('ended'),
    { type: 'call', event: 'summary', call_id: 'c1', summary: 'done', outcome: 'message_taken' },
  ]);
  assert.equal(v.state, 'ended');
  assert.equal(v.endedAt, 2_000_000);
  assert.equal(v.summary.outcome, 'message_taken');
  assert.equal(v.transcript.filter((l) => l.mark).length, 3);
});

test('a new call id replaces the old call; stray events for other ids are ignored', () => {
  let v = fold([state('handling'), line('caller', 'a', false)]);
  v = reduceCall(v, parseCallEvent({ ...line('caller', 'x', false), call_id: 'other' }), 1);
  assert.equal(speech(v).length, 1);
  v = reduceCall(v, parseCallEvent(state('ringing', { call_id: 'c2' })), 1);
  assert.equal(v.callId, 'c2');
  assert.equal(speech(v).length, 0);
});

test('speaking events do not touch the view', () => {
  const v = fold([state('handling')]);
  const ev = parseCallEvent({ type: 'call', event: 'speaking', call_id: 'c1', level: 0.5 });
  assert.equal(reduceCall(v, ev, 9), v);
});
