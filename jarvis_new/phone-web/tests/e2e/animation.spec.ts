import { expect, test, type WebSocketRoute } from '@playwright/test';
import { openApp, settled } from './mock';

test.use({ viewport: { width: 393, height: 851 }, video: { mode: 'on', size: { width: 393, height: 851 } } });

test('ASCII reactor: idle, listening, thinking, speaking and reversible deck morph', async ({ page }) => {
  let socket: WebSocketRoute | undefined;
  await page.routeWebSocket(/\/voice\?token=/, (ws) => {
    socket = ws;
    ws.onMessage(() => {});
    ws.send(JSON.stringify({ topic: 'hello', payload: { identity: 'phone', room: 'r', mode: 'speaker' } }));
  });
  await openApp(page);
  await page.waitForTimeout(900);
  const ring = page.locator('[data-morph="core.ring"]');
  const before = await ring.screenshot();
  await page.waitForTimeout(350);
  expect((await ring.screenshot()).equals(before)).toBe(false);
  await page.screenshot({ path: 'test-results/shots/ascii-idle.png' });
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.locator('.strip')).toContainText('CALLLIVE');
  for (const agent of ['listening', 'thinking', 'speaking']) {
    socket!.send(JSON.stringify({ topic: 'state', payload: { agent } }));
    await page.waitForTimeout(1100);
    await page.screenshot({ path: `test-results/shots/ascii-${agent}.png` });
  }
  // Exercise the real morph in both directions and a reversal in flight.
  await page.getByLabel('Open deck').click();
  await page.waitForTimeout(140);
  await page.getByLabel('Back to core').click({ force: true });
  await settled(page);
  await page.getByLabel('Open deck').click();
  await settled(page);
  await page.waitForTimeout(500);
  await page.getByLabel('Back to core').click();
  await settled(page);
  await page.getByRole('button', { name: 'End call', exact: true }).click();
});

test('reduced-motion ASCII canvas stays still', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openApp(page);
  await page.waitForTimeout(600);
  const ring = page.locator('[data-morph="core.ring"]');
  const first = await ring.screenshot();
  await page.waitForTimeout(350);
  expect((await ring.screenshot()).equals(first)).toBe(true);
});

test('changing reduced-motion while open freezes and resumes the reactor', async ({ page }) => {
  await openApp(page);
  const ring = page.locator('[data-morph="core.ring"]');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.waitForTimeout(400);
  const frozen = await ring.screenshot();
  await page.waitForTimeout(300);
  expect((await ring.screenshot()).equals(frozen)).toBe(true);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.waitForTimeout(300);
  expect((await ring.screenshot()).equals(frozen)).toBe(false);
});

test('ASCII field works when OffscreenCanvas is unavailable', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, 'OffscreenCanvas', { value: undefined }));
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await openApp(page);
  const ring = page.locator('[data-morph="core.ring"]');
  const first = await ring.screenshot();
  await page.waitForTimeout(350);
  expect((await ring.screenshot()).equals(first)).toBe(false);
  await page.getByLabel('Open deck').click();
  await settled(page);
  expect(errors).toEqual([]);
});
