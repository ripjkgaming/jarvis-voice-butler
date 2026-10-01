// Execute generated KWin scripts without touching the user's session bus/desktop.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

function signal() {
  const callbacks = [];
  return { connect(fn) { callbacks.push(fn); }, emit(...args) { callbacks.forEach(fn => fn(...args)); } };
}

const outputs = new Map(input.outputs.map(o => [o.name, {
  name: o.name, geometry: { ...o.geometry }, scale: o.scale || 1,
  panel: o.panel || 0, reserved: o.reserved ?? o.panel ?? 0,
}]));
const timers = [];
const moves = [];
const messages = [];
const windows = [];
function window(config) {
  let frame = { ...config.geometry };
  const w = {
    id: config.id, caption: config.caption || 'Jarvis · School',
    resourceClass: config.resourceClass || 'jarvis-shell',
    output: outputs.get(config.output), normalWindow: !config.dock,
    dock: !!config.dock, minimized: false, desktops: [1], onAllDesktops: false,
    writes: 0, clientGeometry: { ...(config.clientGeometry || frame) },
  };
  for (const name of ['frameGeometryChanged', 'outputChanged', 'minimizedChanged', 'desktopsChanged', 'captionChanged']) w[name] = signal();
  Object.defineProperty(w, 'frameGeometry', {
    get() { return frame; },
    set(value) { frame = { ...value }; w.clientGeometry = { ...frame }; w.writes += 1; },
  });
  windows.push(w);
  return w;
}

const hud = window({ id: 'hud', ...input.hud });
// A similarly named non-Jarvis app must never be resized by these scripts.
window({ id: 'other', caption: 'Jarvis', resourceClass: 'text-editor', output: input.hud.output,
  geometry: { x: 120, y: 120, width: 500, height: 400 } });
for (const o of outputs.values()) {
  if (o.panel > 0) window({ id: 'panel-' + o.name, caption: 'Plasma', resourceClass: 'plasmashell',
    output: o.name, dock: true, geometry: { x: o.geometry.x, y: o.geometry.y + o.geometry.height - o.panel,
      width: o.geometry.width, height: o.panel } });
}
const workspace = {
  screens: [...outputs.values()], activeScreen: outputs.get(input.active), currentDesktop: 1,
  windowList() { return windows.filter(w => !w.dock || workspace.screens.includes(w.output)); },
  stackingOrder: windows, raiseWindow() {},
  clientArea(kind, outputOrWindow) {
    const o = outputOrWindow.geometry ? outputOrWindow : outputOrWindow.output;
    return { ...o.geometry, height: o.geometry.height - (kind === 1 ? o.reserved : 0) };
  },
  sendClientToScreen(w, target) {
    // The real compositor can update w.output only after this script finishes.
    // Reading the old window's area immediately after this call is a regression.
    moves.push({ id: w.id, target: target.name });
    w.pendingOutput = target;
  },
};
if (!input.noScreenOrder) workspace.screenOrder = (input.order || []).map(n => outputs.get(n));
for (const name of ['screensChanged', 'screenOrderChanged', 'stackingOrderChanged', 'virtualScreenGeometryChanged',
  'windowAdded', 'windowRemoved', 'currentDesktopChanged']) workspace[name] = signal();

function settle() {
  for (let i = 0; i < 20; i++) {
    for (const w of windows) {
      if (w.pendingOutput) { w.output = w.pendingOutput; delete w.pendingOutput; }
    }
    const pending = timers.filter(t => t.pending);
    if (!pending.length) return;
    for (const t of pending) { t.pending = false; t.timeout.emit(); }
  }
  throw new Error('KWin script failed to settle after 20 timer rounds');
}

function snapshot() {
  return { windows: Object.fromEntries(windows.map(w => [w.id, {
    output: w.output.name, geometry: w.frameGeometry, keepAbove: !!w.keepAbove,
    noBorder: !!w.noBorder, skipTaskbar: !!w.skipTaskbar, skipPager: !!w.skipPager,
    skipSwitcher: !!w.skipSwitcher, onAllDesktops: !!w.onAllDesktops, writes: w.writes,
  }])), moves: [...moves], messages: [...messages] };
}

const results = [];
for (const step of input.steps) {
  if (step.run) {
    const context = {
      workspace, KWin: { FullScreenArea: 0, MaximizeArea: 1 },
      QTimer: class {
        constructor() { this.timeout = signal(); this.pending = false; timers.push(this); }
        start() { this.pending = true; }
        stop() { this.pending = false; }
      },
      callDBus(...args) { messages.push({ method: args[3], value: JSON.parse(args[4]) }); },
    };
    vm.runInNewContext('const JK_T = ' + (input.thickness || 0) + ';\n' + input.scripts[step.run], context,
      { filename: 'generated-' + step.run + '.js', timeout: 1000 });
  }
  if (step.order) workspace.screenOrder = step.order.map(n => outputs.get(n));
  if (step.active) workspace.activeScreen = outputs.get(step.active);
  if (step.screens) workspace.screens = step.screens.map(n => outputs.get(n));
  if (step.layout) {
    const o = outputs.get(step.layout.name);
    Object.assign(o, step.layout);
    const panel = windows.find(w => w.id === 'panel-' + o.name);
    if (panel) panel.frameGeometry = { x: o.geometry.x, y: o.geometry.y + o.geometry.height - o.panel,
      width: o.geometry.width, height: o.panel };
  }
  if (step.move) {
    hud.output = outputs.get(step.move.output);
    hud.frameGeometry = step.move.geometry;
    hud.outputChanged.emit();
  }
  if (step.signal) workspace[step.signal].emit();
  settle();
  results.push(snapshot());
}
process.stdout.write(JSON.stringify(results));
