const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const scope = { exports: {}, Date };
vm.runInNewContext(
  ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/voice-caption.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText,
  scope
);
const { voiceCaptionFrom } = scope.exports;
const user = (stamp) => [{ ts: stamp, role: 'sir', text: 'The user spoke after the greeting.' }];
const greeting = (stamp, done = true) => ({ id: 'greeting', ts: stamp, text: 'Hello, Sir.', done });

test('a finished greeting cannot hide a user line in the same legacy timestamp second', () => {
  const line = voiceCaptionFrom(user(100), greeting(100.4), 101);
  assert.equal(line.role, 'sir');
  assert.equal(line.live, false);
});

test('a newer user line supersedes an interrupted greeting that never flushed', () => {
  const line = voiceCaptionFrom(user(105), greeting(100.4, false), 106);
  assert.equal(line.role, 'sir');
  assert.equal(line.text, 'The user spoke after the greeting.');
});

test('word-synced Jarvis speech resumes when its timestamp is observably newer', () => {
  for (const done of [false, true]) {
    const line = voiceCaptionFrom(user(100), greeting(101.2, done), 102);
    assert.equal(line.role, 'jarvis');
    assert.equal(line.live, true);
    assert.equal(line.key, 'lgreeting');
  }
});

test('fractional log timestamps preserve ordering within a second', () => {
  assert.equal(voiceCaptionFrom(user(100.5), greeting(100.4), 101).role, 'sir');
  assert.equal(voiceCaptionFrom(user(100.5), greeting(100.6, false), 101).role, 'jarvis');
});

test('synced assistant words win over their own completed log entry', () => {
  const captions = [{ ts: 100, role: 'jarvis', text: 'Hello, Sir. How can I help?' }];
  assert.equal(voiceCaptionFrom(captions, greeting(100.4, false), 101).text, 'Hello, Sir.');
});

test('freshness policy applies to both sources and empty live text never masks a user', () => {
  assert.equal(voiceCaptionFrom(user(100), greeting(100.4), 131, 30), null);
  assert.equal(voiceCaptionFrom([], greeting(100.4, false), 131, 30), null);
  assert.equal(voiceCaptionFrom([], greeting(100.4, false), 131, 120).role, 'jarvis');
  assert.equal(voiceCaptionFrom(user(100), { ...greeting(101), text: '  ' }, 102).role, 'sir');
});
