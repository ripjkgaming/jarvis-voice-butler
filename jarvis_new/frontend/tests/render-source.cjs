// Expose real renderer functions to a headless pixel probe without React/native IPC.
// No test exports or instrumentation are included in the production bundle.
const fs = require('node:fs');
const ts = require('typescript');
const [source, kind, name] = process.argv.slice(2);
const internals = kind === 'entry' ? 'drawScene, drawTracer, drawPool, buildRim, flow' : 'drawScene, makePen';
const input = fs.readFileSync(source, 'utf8') + `\nexport const renderProbe = { ${internals} };\n`;
const compiled = ts.transpileModule(input, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    jsx: ts.JsxEmit.ReactJSX,
  },
}).outputText;
process.stdout.write(
  `(() => { const exports = {}; const require = name => name.endsWith('school-transition') ? window[${JSON.stringify(name + 'Entry')}] : {}; ${compiled}\nwindow[${JSON.stringify(name + (kind === 'entry' ? 'Entry' : 'Return'))}] = exports; })();`
);
