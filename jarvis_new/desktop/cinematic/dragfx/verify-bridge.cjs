const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const signal = () => { const slots = []; return {connect: f => slots.push(f), emit: (...args) => slots.forEach(f => f(...args))}; };
const window = () => ({normalWindow: true, move: true, resize: false, fullScreen: false,
    frameGeometry: {x: -800, y: 200, width: 600, height: 400},
    interactiveMoveResizeStarted: signal(), interactiveMoveResizeStepped: signal(),
    interactiveMoveResizeFinished: signal(), frameGeometryChanged: signal()});
const a = window(), b = window(), reports = [];
const context = {workspace: {cursorPos: {x: 0, y: 0}, windowList: () => [a, b], windowAdded: signal()},
    callDBus: (...args) => reports.push(JSON.parse(args.at(-1)))};
vm.runInNewContext(fs.readFileSync(__dirname + '/drag-kwin.js', 'utf8'), context);
a.interactiveMoveResizeStarted.emit();
a.interactiveMoveResizeFinished.emit();
a.frameGeometry = {x: 1920, y: 0, width: 960, height: 1040};
a.frameGeometryChanged.emit();
assert.deepEqual(reports.map(r => r.s), ['start', 'end', 'target']);
assert.equal(reports[2].x, 1920);
assert.equal(reports[2].w, 960);
assert.equal(reports[0].id, reports[2].id);
b.move = false; b.resize = true;
b.interactiveMoveResizeStarted.emit(); b.interactiveMoveResizeFinished.emit();
assert.equal(reports.length, 3, 'resize must not replace the reform target');
b.move = true; b.resize = false; b.interactiveMoveResizeStarted.emit();
assert.notEqual(reports.at(-1).id, reports[0].id);
console.log('PASS: reform tracks committed geometry; resize ignored; window IDs are distinct.');
