import { execSync } from 'node:child_process';
import { readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';

const root = __dirname;
const sha = (() => {
  try {
    return execSync('git rev-parse --short HEAD', { cwd: root }).toString().trim();
  } catch {
    return 'nogit';
  }
})();
export const BUILD_ID = process.env.LINK_BUILD_ID || `${sha}-${Date.now().toString(36)}`;
const ORIGIN_HOST = process.env.LINK_ORIGIN_HOST || 'ripjk.tailea4e7f.ts.net';

const CSP = [
  "default-src 'self'",
  `connect-src 'self' wss://${ORIGIN_HOST}`,
  "img-src 'self' data:",
  "font-src 'self'",
  "style-src 'self'",
  "script-src 'self'",
  "worker-src 'self'",
  "manifest-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

/** Stamps BUILD_ID + CSP into index.html and the precache list into sw.js. */
function linkBuild(): Plugin {
  return {
    name: 'locke-link-build',
    apply: 'build',
    enforce: 'post',
    transformIndexHtml(html) {
      return html
        .replace('<!--CSP-->', `<meta http-equiv="Content-Security-Policy" content="${CSP}">`)
        .replace('<!--BUILD-->', `<meta name="link-build" content="${BUILD_ID}">`);
    },
    generateBundle(_opts, bundle) {
      const pub = resolve(root, 'public');
      const files = [
        ...Object.keys(bundle).filter((f) => f !== 'sw.js' && !f.endsWith('.map')),
        ...walk(pub).map((p) => relative(pub, p)),
      ].filter((f) => !f.startsWith('.') && !f.endsWith('.LICENSE.txt'));
      const list = ['./', ...files.filter((f) => f !== 'index.html').map((f) => './' + f)];
      const sw = bundle['sw.js'];
      if (!sw || sw.type !== 'chunk') throw new Error('sw.js missing from bundle');
      if (!sw.code.includes('"__PRECACHE__"')) throw new Error('sw.js precache marker missing');
      sw.code = sw.code.replace('"__PRECACHE__"', JSON.stringify(list));
    },
  };
}

export default defineConfig({
  root,
  base: './',
  resolve: {
    alias: { '@hud/ascii': resolve(root, '../frontend/lib/ascii.ts') },
  },
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  server: { fs: { allow: [resolve(root, '..')] } },
  worker: { format: 'es' },
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: true,
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    sourcemap: false,
    minify: process.env.LINK_NOMINIFY ? false : 'esbuild',
    rollupOptions: {
      input: { index: resolve(root, 'index.html'), sw: resolve(root, 'src/sw.ts') },
      output: {
        entryFileNames: (chunk) => (chunk.name === 'sw' ? 'sw.js' : 'assets/[name]-[hash].js'),
      },
    },
  },
  plugins: [linkBuild()],
  // LINK_BRIDGE=http://127.0.0.1:4399 proxies /api to a (throwaway) bridge for local runs.
  preview: {
    port: 4173,
    strictPort: true,
    proxy: process.env.LINK_BRIDGE ? { '/api': { target: process.env.LINK_BRIDGE, changeOrigin: false } } : undefined,
  },
});
