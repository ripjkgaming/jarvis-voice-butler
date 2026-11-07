import { expect, test } from '@playwright/test';
import { Mock, openApp, settled } from './mock';

test.use({ viewport: { width: 390, height: 844 } });

test('visual: gate unpaired, gate cold, core, deck', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  const m = new Mock();
  await m.install(page);
  await page.goto('/');
  await expect(page.locator('.pairbox')).toBeVisible();
  await page.waitForTimeout(700);
  await page.screenshot({ path: 'test-results/shots/gate-unpaired.png' });
  m.paired = true;
  await page.reload();
  await expect(page.getByLabel('Passphrase')).toBeEnabled();
  await page.waitForTimeout(500);
  await page.screenshot({ path: 'test-results/shots/gate-cold.png' });
  const p2 = await page.context().newPage();
  await openApp(p2, new Mock());
  await p2.waitForTimeout(1500);
  await p2.screenshot({ path: 'test-results/shots/core.png' });
  await p2.getByLabel('Open deck').click();
  await settled(p2);
  await p2.waitForTimeout(400);
  await p2.screenshot({ path: 'test-results/shots/deck.png' });
  await p2.getByRole('button', { name: /Laptop power/ }).click();
  await p2.waitForTimeout(600);
  await p2.screenshot({ path: 'test-results/shots/deck-power.png' });
  expect(errors.filter((e) => !/favicon|Failed to load resource/.test(e))).toEqual([]);
});
