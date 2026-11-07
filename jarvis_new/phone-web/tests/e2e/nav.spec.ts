import { expect, test, type Page } from '@playwright/test';
import { openApp, settled } from './mock';

const shown = (p: string) => `[data-page="${p}"]:not([data-hidden])`;

test('⌗ opens DECK, header ring returns, back button reverses', async ({ page }) => {
  await openApp(page);
  await page.getByLabel('Open deck').click();
  await settled(page);
  await expect(page.locator(shown('deck'))).toHaveCount(1);
  await expect(page.locator(shown('core'))).toHaveCount(0);
  await page.getByLabel('Back to core').click();
  await settled(page);
  await expect(page.locator(shown('core'))).toHaveCount(1);
  await page.getByLabel('Open deck').click();
  await settled(page);
  await page.goBack();
  await settled(page);
  await expect(page.locator(shown('core'))).toHaveCount(1);
});

test('typing "deck" and "core" navigates locally without a bridge call', async ({ page }) => {
  const m = await openApp(page);
  const cmd = page.getByLabel('Command');
  await cmd.fill('open the deck');
  await cmd.press('Enter');
  await settled(page);
  await expect(page.locator(shown('deck'))).toHaveCount(1);
  expect(m.commands).toEqual([]);
});

test('a typed command round-trips with the ⚡ route latency', async ({ page }) => {
  const m = await openApp(page);
  const cmd = page.getByLabel('Command');
  await cmd.fill('volume up');
  await cmd.press('Enter');
  await expect(page.locator('.cap.jarvis').last()).toContainText('Done: volume up');
  await expect(page.locator('.cap.jarvis .via').last()).toContainText(/\d+ms/);
  expect(m.commands).toEqual(['volume up']);
});

/** Sample core.ring's computed transform every frame for `ms`, running `act` first. */
async function sample(page: Page, interrupt: boolean) {
  return page.evaluate(async (interrupt) => {
    const ring = document.querySelector('[data-morph="core.ring"]') as HTMLElement;
    const pts: number[][] = [];
    (document.querySelector('[aria-label="Open deck"]') as HTMLElement).click();
    const t0 = performance.now();
    let fired = false;
    await new Promise<void>((done) => {
      const f = () => {
        const m = new DOMMatrix(getComputedStyle(ring).transform === 'none' ? undefined : getComputedStyle(ring).transform);
        pts.push([m.e, m.f, Math.hypot(m.a, m.b)]);
        if (interrupt && !fired && performance.now() - t0 > 120) {
          fired = true;
          (document.querySelector('[aria-label="Back to core"]') as HTMLElement).click();
        }
        if (performance.now() - t0 < 700) requestAnimationFrame(f);
        else done();
      };
      requestAnimationFrame(f);
    });
    return pts;
  }, interrupt);
}
const maxStep = (pts: number[][]) =>
  Math.max(...pts.slice(1).map((p, i) => Math.max(Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]), Math.abs(p[2] - pts[i][2]) * 100)));

test('interrupting CORE→DECK mid-flight reverses without a jump', async ({ page }) => {
  await openApp(page);
  const plain = await sample(page, false);
  const base = maxStep(plain);
  expect(base).toBeGreaterThan(1);
  await page.getByLabel('Back to core').click();
  await settled(page);
  const pts = await sample(page, true);
  expect(maxStep(pts)).toBeLessThanOrEqual(2 * base);
  await settled(page);
  const end = pts[pts.length - 1];
  expect(Math.abs(end[0]) + Math.abs(end[1])).toBeLessThan(0.5);
  expect(Math.abs(end[2] - 1)).toBeLessThan(0.005);
  await expect(page.locator(shown('core'))).toHaveCount(1);
});

test('reduced motion: pages only crossfade, nothing transforms', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openApp(page);
  const transforms = await page.evaluate(async () => {
    const els = [...document.querySelectorAll<HTMLElement>('[data-morph^="core."], [data-morph^="deck."]')];
    const seen = new Set<string>();
    (document.querySelector('[aria-label="Open deck"]') as HTMLElement).click();
    const t0 = performance.now();
    await new Promise<void>((done) => {
      const f = () => {
        for (const e of els) seen.add(e.style.transform || 'none');
        if (performance.now() - t0 < 300) requestAnimationFrame(f);
        else done();
      };
      requestAnimationFrame(f);
    });
    return [...seen];
  });
  expect(transforms.filter((t) => t !== 'none' && t !== '')).toEqual([]);
  await expect(page.locator(shown('deck'))).toHaveCount(1);
});
