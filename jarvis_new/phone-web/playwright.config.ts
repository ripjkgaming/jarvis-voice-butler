import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 45_000,
  fullyParallel: true,
  workers: 2, // video, fake microphone and CPU traces share the desktop GPU
  reporter: [['list']],
  use: {
    ...devices['Pixel 7'],
    baseURL: 'http://localhost:4173',
    serviceWorkers: 'block',
    bypassCSP: true,
    launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] },
  },
  // Measure frame budgets after functional tests release their browsers.
  projects: [
    { name: 'pixel7', testIgnore: /perf\.spec\.ts/, use: { browserName: 'chromium' } },
    { name: 'performance', testMatch: /perf\.spec\.ts/, dependencies: ['pixel7'], use: { browserName: 'chromium' } },
  ],
  webServer: { command: 'npx vite preview --port 4173 --strictPort', url: 'http://localhost:4173', reuseExistingServer: true, timeout: 30_000 },
});
