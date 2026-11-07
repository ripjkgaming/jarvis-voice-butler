import { resolve } from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: { alias: { '@hud/ascii': resolve(__dirname, '../frontend/lib/ascii.ts') } },
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  define: { __BUILD_ID__: '"test"' },
  test: { include: ['tests/unit/**/*.test.ts'], environment: 'node' },
});
