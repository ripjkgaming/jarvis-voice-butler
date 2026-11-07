import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { Mock, openApp, settled } from './mock';

async function serious(page: Page, include: string) {
  const r = await new AxeBuilder({ page }).include(include).include('.strip').analyze();
  return r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' | ')}`);
}

test('axe: GATE has no serious or critical violations', async ({ page }) => {
  const m = new Mock();
  m.paired = true;
  await m.install(page);
  await page.goto('/');
  await expect(page.getByLabel('Passphrase')).toBeEnabled();
  expect(await serious(page, '[data-page="gate"]')).toEqual([]);
});

test('axe: CORE and DECK have no serious or critical violations', async ({ page }) => {
  await openApp(page);
  expect(await serious(page, '[data-page="core"]')).toEqual([]);
  await page.getByLabel('Open deck').click();
  await settled(page);
  await page.getByRole('button', { name: /Laptop power/ }).click();
  await page.waitForTimeout(500);
  expect(await serious(page, '[data-page="deck"]')).toEqual([]);
});

test('touch targets are at least 48x48', async ({ page }) => {
  await openApp(page);
  for (const name of ['Open deck', 'Start call', 'Send']) {
    const b = (await page.getByRole('button', { name }).boundingBox())!;
    expect(Math.min(b.width, b.height), name).toBeGreaterThanOrEqual(48);
  }
});
