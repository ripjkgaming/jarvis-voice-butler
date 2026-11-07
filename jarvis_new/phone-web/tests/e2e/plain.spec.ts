import { expect, test } from '@playwright/test';
import { openApp } from './mock';

for (const width of [360, 412]) {
  test(`plain interface at ${width}px keeps controls and has no animation`, async ({ page }) => {
    await page.setViewportSize({ width, height: 760 });
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    const mock = await openApp(page);
    await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
    await page.getByLabel('Command').fill('status');
    await page.getByLabel('Command').press('Enter');
    await expect(page.locator('.cap.jarvis').last()).toContainText('Done: status');
    expect(mock.commands).toEqual(['status']);
    await page.getByLabel('Open deck').click();
    await expect(page.locator('[data-page="deck"]')).toBeVisible();
    await expect(page.locator('[data-page="core"]')).toBeHidden();
    await expect(page.getByRole('button', { name: /Lock the laptop/ })).toBeVisible();
    await expect(page.locator('canvas')).toHaveCount(0);
    const motion = await page.evaluate(() => ({
      animations: document.getAnimations().length,
      animated: [...document.querySelectorAll('*')].filter(el => {
        const css = getComputedStyle(el);
        return css.animationName !== 'none' || css.transitionDuration.split(',').some(t => parseFloat(t) > 0);
      }).length,
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    expect(motion).toEqual({ animations: 0, animated: 0, overflow: false });
    await page.screenshot({ path: `test-results/shots/plain-controls-${width}.png`, fullPage: true });
    await page.getByLabel('Back to core').click();
    await expect(page.getByLabel('Command')).toBeVisible();
    await page.screenshot({ path: `test-results/shots/plain-home-${width}.png`, fullPage: true });
    expect(errors).toEqual([]);
  });
}
