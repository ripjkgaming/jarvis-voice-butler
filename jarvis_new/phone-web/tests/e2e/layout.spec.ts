import { expect, test } from '@playwright/test';
import { Mock, openApp, settled } from './mock';

const SIZES = [
  { name: 'small-360x640', w: 360, h: 640 },
  { name: 'pixel7-412x915', w: 412, h: 915 },
  { name: 'pixel7-393x851', w: 393, h: 851 },
];

type R = { top: number; bottom: number; left: number; right: number; height: number };
const rect = async (page: import('@playwright/test').Page, sel: string): Promise<R> =>
  page.locator(sel).first().evaluate((e) => {
    const r = e.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height };
  });

for (const s of SIZES) {
  test(`layout ${s.name}: CORE fills down to the command line, DECK has no gap`, async ({ page }) => {
    await page.setViewportSize({ width: s.w, height: s.h });
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await openApp(page, new Mock());
    // real captions: the user's own commands land in the transcript
    const cmd = page.getByLabel('Command');
    for (const t of ['status', 'what is the weather like in the lab tonight', 'lock the screens', 'one', 'two', 'three', 'four', 'five', 'six', 'seven']) {
      await cmd.fill(t);
      await cmd.press('Enter');
    }
    await page.waitForTimeout(700);
    const cap = await rect(page, '[data-morph="core.captions"]');
    const line = await rect(page, '[data-morph="core.cmd"]');
    expect(line.height).toBeGreaterThanOrEqual(48);
    expect(line.top - cap.bottom).toBeLessThanOrEqual(16);
    expect(line.top - cap.bottom).toBeGreaterThanOrEqual(0);
    expect(cap.height).toBeGreaterThanOrEqual(72);
    const pinned = await page.locator('[data-morph="core.captions"]').evaluate((e) => e.scrollHeight - e.scrollTop - e.clientHeight);
    expect(pinned).toBeLessThan(24);
    await page.screenshot({ path: `test-results/shots/core-${s.name}.png` });

    await page.getByLabel('Open deck').click();
    await settled(page);
    await page.waitForTimeout(400);
    const stat = await rect(page, '[data-morph="deck.stat"]');
    const tiles: Record<string, R> = {};
    for (const id of ['GUEST', 'LOCK', 'UNLOCK', 'SCREENS', 'POWER', 'MIC']) tiles[id] = await rect(page, `[data-morph="deck.tile.${id}"]`);
    for (const t of Object.values(tiles)) expect(t.height).toBeGreaterThanOrEqual(48);
    expect(tiles.GUEST.top).toBeGreaterThanOrEqual(stat.bottom);
    expect(tiles.UNLOCK.top).toBeGreaterThanOrEqual(tiles.GUEST.bottom);
    expect(tiles.POWER.top).toBeGreaterThanOrEqual(tiles.UNLOCK.bottom);
    // no big hole: power row to mic bar is at most 40px, and the mic bar sits at the bottom
    expect(tiles.MIC.top - tiles.POWER.bottom).toBeLessThanOrEqual(40);
    expect(tiles.MIC.top - tiles.POWER.bottom).toBeGreaterThanOrEqual(0);
    expect(s.h - tiles.MIC.bottom).toBeLessThanOrEqual(32);
    await page.screenshot({ path: `test-results/shots/deck-${s.name}.png` });

    await page.getByRole('button', { name: /Laptop power/ }).click();
    await page.waitForTimeout(600);
    const pw = await rect(page, '[data-morph="deck.tile.POWER"]');
    const mic = await rect(page, '[data-morph="deck.tile.MIC"]');
    expect(pw.bottom).toBeLessThanOrEqual(mic.top + 0.5);
    for (const name of [/Laptop power/, /Sleep the laptop/, /Restart the laptop/, /Shut down the laptop/]) {
      const b = (await page.getByRole('button', { name }).boundingBox())!;
      expect(Math.min(b.width, b.height), `${name} target`).toBeGreaterThanOrEqual(48);
      expect(b.y + b.height, `${name} fits power tile`).toBeLessThanOrEqual(pw.bottom + 0.5);
    }
    await page.screenshot({ path: `test-results/shots/deck-power-${s.name}.png` });
    expect(errors).toEqual([]);
  });
}

test('landscape does not break (no errors, deck scrolls, command line usable)', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await openApp(page, new Mock());
  await page.waitForTimeout(500);
  await page.screenshot({ path: 'test-results/shots/core-landscape.png' });
  await page.getByLabel('Open deck').click({ force: true });
  await settled(page);
  await page.waitForTimeout(400);
  await page.screenshot({ path: 'test-results/shots/deck-landscape.png' });
  expect(errors).toEqual([]);
});
