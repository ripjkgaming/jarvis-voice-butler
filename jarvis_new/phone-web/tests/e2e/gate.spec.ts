import { expect, test } from '@playwright/test';
import { Mock, settled, virtualAuthenticator } from './mock';

const core = '[data-page="core"]:not([data-hidden])';

test('pair via #pair → passkey registration → passphrase → CORE', async ({ page }) => {
  const m = new Mock();
  await m.install(page);
  await virtualAuthenticator(page);
  await page.goto('/#pair=ABCDE12345');
  await expect(page.locator('.glyphrow')).toContainText('Enter your passphrase to unlock.', { timeout: 10000 });
  expect(m.passkey).toBe(true);
  expect(page.url()).not.toContain('pair='); // fragment scrubbed
  await page.getByLabel('Passphrase').fill('open sesame');
  await page.getByLabel('Passphrase').press('Enter');
  await page.locator(core).waitFor();
  await settled(page);
  await expect(page.getByRole('button', { name: 'Start call' })).toBeVisible();
});

test('unpaired: instructions shown, line accepts a pasted pairing link', async ({ page }) => {
  const m = new Mock();
  await m.install(page);
  await virtualAuthenticator(page);
  await page.goto('/');
  const line = page.getByLabel('Passphrase');
  await expect(line).toBeEnabled();
  await line.fill('https://ripjk.tailea4e7f.ts.net/#pair=ABCDE12345');
  await line.press('Enter');
  await expect(page.locator('.glyphrow')).toContainText('Enter your passphrase to unlock.', { timeout: 10000 });
  expect(m.paired).toBe(true);
});

test('unpaired: instructions shown', async ({ page }) => {
  await new Mock().install(page);
  await page.goto('/');
  await expect(page.locator('.glyphrow')).toContainText('Pair this phone');
  await expect(page.locator('.pairbox')).toContainText('phone_pair.py');
});

test('bad pairing code is reported', async ({ page }) => {
  await new Mock().install(page);
  await page.goto('/#pair=ZZZZZ99999');
  await expect(page.getByRole('alert')).toHaveText('CODE INVALID OR EXPIRED');
});

test('wrong passphrase counts down, then locks out', async ({ page }) => {
  const m = new Mock();
  m.paired = true;
  await m.install(page);
  await page.goto('/');
  const pw = page.getByLabel('Passphrase');
  await expect(pw).toBeEnabled();
  for (const left of [4, 3, 2, 1]) {
    await pw.fill('nope');
    await pw.press('Enter');
    await expect(page.getByRole('alert')).toHaveText(`DENIED · ${left} LEFT`);
  }
  await pw.fill('nope');
  await pw.press('Enter');
  await expect(page.getByRole('alert')).toHaveText(/LOCKOUT (60|59)s/);
  await expect(pw).toBeDisabled();
});

test('soft lock → fingerprint → reopen', async ({ page }) => {
  const m = new Mock();
  m.paired = true;
  m.passkey = true;
  m.gate = 'soft';
  await m.install(page);
  await virtualAuthenticator(page, true);
  await page.goto('/');
  const fp = page.getByRole('button', { name: 'Unlock with fingerprint' });
  await expect(fp).toBeVisible();
  await fp.click();
  await page.locator(core).waitFor();
  expect(m.gate).toBe('open');
});

test('gate lost while open morphs back to GATE', async ({ page }) => {
  const { openApp } = await import('./mock');
  const m = await openApp(page);
  m.gate = 'soft';
  await page.locator('[data-page="gate"]:not([data-hidden])').waitFor({ timeout: 8000 });
  await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeVisible();
});
