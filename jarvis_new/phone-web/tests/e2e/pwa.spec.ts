import { expect, test } from '@playwright/test';
import { Mock } from './mock';

test.use({ serviceWorkers: 'allow' });

test('installable: manifest, icons, controlling service worker, no installability errors', async ({ page }) => {
  await new Mock().install(page);
  let navigations = 0;
  page.on('framenavigated', (frame) => { if (frame === page.mainFrame()) navigations++; });
  await page.goto('/');
  await page.waitForFunction(() => !!navigator.serviceWorker.controller);
  await page.waitForTimeout(250);
  // Claiming the very first worker must not reload and discard a pairing code.
  expect(navigations).toBe(1);
  await page.reload();
  await page.waitForFunction(() => !!navigator.serviceWorker.controller);
  const cdp = await page.context().newCDPSession(page);
  const { installabilityErrors } = (await cdp.send('Page.getInstallabilityErrors')) as { installabilityErrors: { errorId: string }[] };
  expect(installabilityErrors.map((e) => e.errorId)).toEqual([]);
  const manifest = await (await page.request.get('/manifest.webmanifest')).json();
  expect(manifest).toMatchObject({ name: 'LOCKE//LINK', short_name: 'LINK', id: '/', start_url: '/?src=pwa', display: 'standalone', orientation: 'portrait' });
  expect(manifest.icons.map((i: { purpose: string }) => i.purpose)).toEqual(['any', 'any', 'maskable', 'monochrome']);
  for (const i of manifest.icons) expect((await page.request.get('/' + i.src)).status()).toBe(200);
  // App shell works offline from the precache; /api is never cached.
  const keys = await page.evaluate(async () => {
    const names = await caches.keys();
    const c = await caches.open(names.find((n) => n.startsWith('link-'))!);
    return (await c.keys()).map((r) => new URL(r.url).pathname);
  });
  expect(keys).toContain('/');
  expect(keys.some((k) => k.startsWith('/api'))).toBe(false);
  await page.context().setOffline(true);
  await page.reload();
  await expect(page.locator('.glyphrow')).toBeVisible();
  await page.context().setOffline(false);
});
