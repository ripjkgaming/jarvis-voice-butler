import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { openApp } from './mock';

type Hub = { sockets: WebSocketRoute[]; frames: number[]; urls: string[] };

async function hub(page: Page): Promise<Hub> {
  const h: Hub = { sockets: [], frames: [], urls: [] };
  await page.routeWebSocket(/\/voice\?token=/, (ws) => {
    h.sockets.push(ws);
    h.urls.push(ws.url());
    ws.onMessage((m) => {
      if (typeof m !== 'string') h.frames.push(m.length);
    });
    ws.send(JSON.stringify({ topic: 'hello', payload: { identity: 'phone', room: 'r', mode: 'speaker' } }));
  });
  return h;
}
const send = (ws: WebSocketRoute, topic: string, payload: unknown) => ws.send(JSON.stringify({ topic, payload }));
const glyph = (page: Page) => page.evaluate(() => (window as never as { __link: { markModel: { glyph: string } } }).__link.markModel.glyph);

test('call: connect, state/caption/audio/flush, mic frames, 4001 re-mint, drop + reconnect', async ({ page }) => {
  const h = await hub(page);
  const m = await openApp(page);
  await page.getByRole('button', { name: 'Start call' }).click();
  await expect(page.locator('.strip')).toContainText('CALLLIVE');
  expect(h.urls[0]).toContain('/voice?token=tok1');
  // mic frames: exactly 1 kind byte + 1024 bytes of PCM16
  await expect.poll(() => h.frames.length, { timeout: 8000 }).toBeGreaterThan(3);
  expect(new Set(h.frames)).toEqual(new Set([1025]));
  const ws = h.sockets[0];
  send(ws, 'state', { agent: 'speaking' });
  await expect.poll(() => glyph(page)).toBe('play');
  send(ws, 'caption', { text: 'Good evening, Sir.', final: true });
  await expect(page.locator('.cap.jarvis').last()).toHaveText('Good evening, Sir.');
  ws.send(Buffer.concat([Buffer.from([2]), Buffer.alloc(4800)]));
  send(ws, 'flush', {});
  send(ws, 'ping', { n: 1 });
  // 4001: re-mint once and reconnect
  ws.close({ code: 4001, reason: 'expired' });
  await expect.poll(() => m.mints).toBe(2);
  await expect.poll(() => h.sockets.length).toBe(2);
  await expect(page.locator('.strip')).toContainText('CALLLIVE');
  // network drop (unlisted code): reconnecting, re-mint after backoff, LINK RESET
  h.sockets[1].close({ code: 4999, reason: 'lost' });
  await expect(page.locator('.strip')).toContainText(/CALL(RCN|LIVE)/);
  await expect.poll(() => m.mints, { timeout: 6000 }).toBe(3);
  await expect(page.locator('.cap.system').last()).toHaveText('── LINK RESET ──');
  await page.getByRole('button', { name: 'End call' }).click();
  await expect(page.locator('.strip')).toContainText('CALLIDLE');
});

test('4003 laptop busy is an error, no retry', async ({ page }) => {
  const h = await hub(page);
  const m = await openApp(page);
  await page.getByRole('button', { name: 'Start call' }).click();
  await expect.poll(() => h.sockets.length).toBe(1);
  h.sockets[0].close({ code: 4003, reason: 'busy' });
  await expect(page.locator('.toast.fail')).toHaveText('LAPTOP BUSY');
  expect(m.mints).toBe(1);
});

test('mark interrupt: listening → speaking mid-morph stays continuous', async ({ page }) => {
  const h = await hub(page);
  await openApp(page);
  await page.getByRole('button', { name: 'Start call' }).click();
  await expect.poll(() => h.sockets.length).toBe(1);
  await expect(page.locator('.strip')).toContainText('CALLLIVE');
  await page.waitForTimeout(800); // settle on star
  const sampling = page.evaluate(async () => {
    const mm = (window as never as { __link: { markModel: { vertex(i: number): [number, number] } } }).__link.markModel;
    const pts: [number, number, number][] = [];
    const t0 = performance.now();
    await new Promise<void>((done) => {
      const f = () => {
        pts.push([...mm.vertex(0), performance.now()]);
        if (performance.now() - t0 < 900) requestAnimationFrame(f);
        else done();
      };
      requestAnimationFrame(f);
    });
    return pts;
  });
  send(h.sockets[0], 'state', { agent: 'listening' });
  await page.waitForTimeout(120);
  send(h.sockets[0], 'state', { agent: 'speaking' });
  const pts = await sampling;
  const steps = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]));
  // Compare movement per 60 Hz frame, allowing for host scheduling gaps.
  const at60Hz = steps.map((step, i) => step * (1000 / 60) / Math.max(1, pts[i + 1][2] - pts[i][2]));
  expect(Math.max(...at60Hz)).toBeLessThan(3);
  expect(Math.max(...steps)).toBeLessThan(10); // reject a teleport even across a delayed frame
  expect(await glyph(page)).toBe('play');
});
