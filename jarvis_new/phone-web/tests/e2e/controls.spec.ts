import { expect, test } from '@playwright/test';
import { openApp, settled } from './mock';

async function deck(page: import('@playwright/test').Page) {
  const m = await openApp(page);
  await page.getByLabel('Open deck').click();
  await settled(page);
  return m;
}
const tile = (page: import('@playwright/test').Page, id: string) => page.locator(`[data-morph="deck.tile.${id}"]`);

test('instant tiles go pending → verified with the real readback', async ({ page }) => {
  const m = await deck(page);
  m.delayControlMs = 400;
  await tile(page, 'GUEST').click();
  await expect(tile(page, 'GUEST')).toHaveAttribute('aria-busy', 'true');
  await expect(tile(page, 'GUEST').locator('.tile-val')).toHaveText('ON');
  await expect(tile(page, 'GUEST')).toHaveAttribute('aria-busy', 'false');
  m.delayControlMs = 50;
  await tile(page, 'LOCK').click();
  await expect(tile(page, 'LOCK').locator('.tile-val')).toHaveText('LOCKED');
  await tile(page, 'SCREENS').click();
  await expect(tile(page, 'SCREENS').locator('.tile-val')).toHaveText('OFF');
  await tile(page, 'MIC').click();
  await expect(tile(page, 'MIC').locator('.tile-val')).toHaveText('MUTED');
  expect(m.controls.map((c) => c.action)).toEqual(['guest_on', 'lock', 'screens_off', 'mic_mute']);
  await expect(page.locator('.strip')).toContainText('GSTON');
});

test('a failure shakes the tile, turns its lamp red and toasts', async ({ page }) => {
  const m = await deck(page);
  m.failNext = 'lock';
  await tile(page, 'LOCK').click();
  await expect(tile(page, 'LOCK')).toHaveClass(/fail/);
  await expect(page.locator('.toast.fail')).toContainText('LOCK FAILED');
});

test('step-up actions open the fingerprint prompt and send an assertion', async ({ page }) => {
  const m = await deck(page);
  m.laptop.locked = true;
  m.laptop.guest = true;
  await page.waitForTimeout(2200); // next poll
  await tile(page, 'UNLOCK').click();
  await expect(tile(page, 'UNLOCK').locator('.tile-val')).toHaveText('OPEN');
  await tile(page, 'GUEST').click();
  await expect(tile(page, 'GUEST').locator('.tile-val')).toHaveText('OFF');
  expect(m.controls).toEqual([
    { action: 'unlock', assertion: true },
    { action: 'guest_off', assertion: true },
  ]);
});

test('POWER: hold RESTART 1200 ms then fingerprint; early release does nothing', async ({ page }) => {
  const m = await deck(page);
  await page.getByRole('button', { name: /Laptop power/ }).click();
  const restart = page.getByRole('button', { name: /Restart the laptop/ });
  await expect(restart).toBeVisible();
  let box = (await restart.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(400);
  await page.mouse.up();
  await page.waitForTimeout(500);
  expect(m.controls).toEqual([]);
  box = (await restart.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(1400);
  await page.mouse.up();
  await expect.poll(() => m.controls).toEqual([{ action: 'restart', assertion: true }]);
  await expect(page.locator('.toast')).toContainText('RESTART SENT · NOT VERIFIABLE');
});

test('offline: lamps grey, controls disabled, then recovery', async ({ page }) => {
  const m = await deck(page);
  m.offline = true;
  await expect(page.locator('.strip')).toContainText('LNKOFF', { timeout: 6000 });
  await expect(tile(page, 'LOCK')).toBeDisabled();
  await expect(page.locator('.strip .t-on')).toHaveCount(0);
  m.offline = false;
  await expect(page.locator('.strip')).toContainText(/LNK\d+ms/, { timeout: 12000 });
  await expect(tile(page, 'LOCK')).toBeEnabled();
});
