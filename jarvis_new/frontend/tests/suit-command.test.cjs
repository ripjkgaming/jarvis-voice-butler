const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const scope = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/suit-diagnostics.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText, scope);
const { consumeSuitCommand, validSuitCommand } = scope.exports;
const cmd = (revision, op = 'open', issued_at = 1000, session = 'bridge-a') => ({ revision, op, issued_at, session });

test('fresh explicit open works; persisted opens do not replay after a reload', () => {
  assert.equal(consumeSuitCommand(cmd(1), null, 1001).open, null);
  assert.equal(consumeSuitCommand(cmd(2, 'open', 1002), null, 1001).open, true);
});
test('out-of-order shared snapshots cannot reopen after acknowledged dismissal', () => {
  const first = consumeSuitCommand(cmd(1), null, 999);
  const closed = consumeSuitCommand(cmd(2, 'close'), first.cursor, 999);
  assert.equal(closed.open, false);
  assert.equal(consumeSuitCommand(cmd(1), closed.cursor, 999).open, null);
  assert.equal(consumeSuitCommand(cmd(2, 'open'), closed.cursor, 999).open, null);
});
test('a fresh repeated open works after local dismissal', () => {
  const old = consumeSuitCommand(cmd(1), null, 999);
  assert.equal(consumeSuitCommand(cmd(2), old.cursor, 999).open, true);
});
test('bridge restart clears an open panel; a fresh command in its new session opens it', () => {
  const old = consumeSuitCommand(cmd(22), null, 999);
  const reset = consumeSuitCommand(cmd(0, 'close', 0, 'bridge-b'), old.cursor, 999);
  assert.equal(reset.open, false);
  assert.equal(consumeSuitCommand(cmd(1, 'open', 1001, 'bridge-b'), reset.cursor, 999).open, true);
});
test('untrusted malformed telemetry never mounts diagnostics', () => {
  for (const value of [null, {}, true, cmd(-1), cmd(1.2), cmd(1, 'toggle'), cmd(1, 'open', NaN), cmd(1, 'open', 0, ''), { ...cmd(1), revision: '1' }]) {
    assert.equal(validSuitCommand(value), false);
    assert.equal(consumeSuitCommand(value, null, 0).open, null);
  }
});
