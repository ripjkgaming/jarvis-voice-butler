import { expect, test } from '@playwright/test';
import { Mock, virtualAuthenticator } from './mock';

test.use({ bypassCSP: false });

test('the CSP meta is enforced and the app runs with zero violations', async ({ page }) => {
  const violations: string[] = [];
  page.on('console', (m) => /Content Security Policy|Refused to/.test(m.text()) && violations.push(m.text()));
  const m = new Mock();
  m.paired = true;
  m.passkey = true;
  m.gate = 'open';
  await m.install(page);
  await virtualAuthenticator(page, true);
  await page.goto('/');
  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content');
  expect(csp).toContain("script-src 'self'");
  expect(csp).toContain("style-src 'self'");
  await page.locator('[data-page="core"]:not([data-hidden])').waitFor();
  await page.getByLabel('Open deck').click();
  await page.locator('[data-page="deck"]:not([data-hidden])').waitFor();
  await page.waitForTimeout(800);
  await page.getByLabel('Back to core').click();
  await page.locator('[data-page="core"]:not([data-hidden])').waitFor();
  await page.waitForTimeout(500);
  expect(violations).toEqual([]);
});
