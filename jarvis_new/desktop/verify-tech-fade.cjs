const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const file = __dirname + '/kwin/effects/locke-tech-fade/contents/code/main.js';
const signal = () => ({connect() {}});
let calls = [], factor = 1, reduced = false, canGrab = true, stolen = true;
const context = {
  print() {},
  effects: {hasActiveFullScreenEffect: false, windowAdded: signal(), windowClosed: signal(), windowDataChanged: signal()},
  effect: {configChanged: signal(), readConfig: () => reduced, grab: () => canGrab,
    isGrabbed: () => stolen, addFragmentShader: () => 7},
  animationTime: n => n * factor,
  animate: a => (calls.push(a), [calls.length * 2, calls.length * 2 + 1]),
  cancel: ids => calls.push({cancel: ids}),
  Effect: {Scale: 1, Opacity: 2, ShaderUniform: 3, WindowClosedGrabRole: 4, WindowAddedGrabRole: 5, MapTexture: 1, Modulate: 4},
  QEasingCurve: {InOutCubic: 1, OutCubic: 2},
};
vm.createContext(context); vm.runInContext(fs.readFileSync(file, 'utf8'), context);
const normal = () => ({visible: true, managed: true, normalWindow: true, windowClass: 'preview preview'});
const fx = context.signalFade;
fx.opened(normal()); fx.closed(normal());
assert.equal(calls[0].duration, 300); assert.equal(calls[1].duration, 220);
assert.equal(calls[0].animations.length, 2);
assert.equal(calls[0].animations[1].from, 0); assert.equal(calls[1].animations[1].to, 0);
for (const prop of ['lockScreen', 'popupWindow', 'desktopWindow', 'dock', 'outline', 'fullScreen']) {
  fx.opened({...normal(), [prop]: true}); fx.closed({...normal(), [prop]: true});
}
for (const windowClass of ['jarvis-watch Jarvis-watch', 'polkit-kde-authentication-agent', 'pinentry', 'kwallet', 'ksmserver', 'plasmashell plasmashell']) {
  fx.opened({...normal(), windowClass}); fx.closed({...normal(), windowClass});
}
fx.closed({...normal(), skipsCloseAnimation: true});
fx.opened({...normal(), visible: false}); fx.closed({...normal(), managed: false});
context.effects.hasActiveFullScreenEffect = true; fx.opened(normal()); fx.closed(normal());
context.effects.hasActiveFullScreenEffect = false;
assert.equal(calls.length, 2);
for (const settings of [[0, false], [1, true]]) {
  [factor, reduced] = settings; fx.configure(); fx.opened(normal()); fx.closed(normal());
}
assert.equal(calls.length, 2);
factor = 1; reduced = false; canGrab = false; fx.configure(); fx.opened(normal()); fx.closed(normal());
assert.equal(calls.length, 2); canGrab = true;
const w = normal(); fx.opened(w); fx.closed(w);
assert.ok(calls.at(-2).cancel); assert.equal(w.lockeSignalOpen, undefined);
fx.changed(w, 4); assert.equal(w.lockeSignalClose, undefined);
const early = normal(); fx.opened(early); early.skipsCloseAnimation = true; fx.closed(early);
assert.equal(early.lockeSignalOpen, undefined);
const held = normal(); fx.opened(held); stolen = false; fx.changed(held, 5); assert.ok(held.lockeSignalOpen);
stolen = true; fx.changed(held, 5); assert.equal(held.lockeSignalOpen, undefined);
factor = 2; fx.configure(); fx.opened(normal()); assert.equal(calls.at(-1).duration, 600);
fx.shader = 0; fx.closed(normal()); assert.equal(calls.at(-1).animations[1].type, 2);
console.log('Signal Aperture: opening/closing, exclusions, reduced motion, global factor, grab takeover, rapid-close cancellation and native fallback passed.');
