import { expect, test } from '@playwright/test';
import { openApp, settled } from './mock';

type Ev = { name: string; ph: string; ts: number; dur?: number; tid: number; args?: unknown };

test('morph budgets under 4x CPU throttle: frame script ≤ 4 ms, no long frames, no layout in rAF', async ({ page }) => {
  await openApp(page);
  // warm-up round trip (JIT, glyph atlases) outside the trace
  await page.getByLabel('Open deck').click();
  await settled(page);
  await page.getByLabel('Back to core').click();
  await settled(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  const events: Ev[] = [];
  cdp.on('Tracing.dataCollected', (d: { value: Ev[] }) => events.push(...d.value));
  const done = new Promise<void>((r) => cdp.once('Tracing.tracingComplete', () => r()));
  await cdp.send('Tracing.start', { categories: 'devtools.timeline,disabled-by-default-devtools.timeline,disabled-by-default-devtools.timeline.stack', transferMode: 'ReportEvents' });
  await page.getByLabel('Open deck').click();
  await settled(page);
  await page.getByLabel('Back to core').click();
  await settled(page);
  await cdp.send('Tracing.end');
  await done;
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  if (process.env.LINK_TRACE) await (await import('node:fs/promises')).writeFile('test-results/frame-trace.json', JSON.stringify(events));
  // The page's main thread only (the ASCII field renders in its own worker thread).
  const mains = new Set(
    events.filter((e) => e.ph === 'M' && e.name === 'thread_name' && (e.args as { name?: string })?.name === 'CrRendererMain').map((e) => e.tid),
  );
  const raf = events.filter((e) => e.name === 'FireAnimationFrame' && e.ph === 'X' && mains.has(e.tid));
  const workerFrames = events.filter((e) => e.name === 'FireAnimationFrame' && e.ph === 'X' && !mains.has(e.tid)).length;
  console.log(`worker field frames=${workerFrames}`);
  expect(raf.length).toBeGreaterThan(20);
  const durs = raf.map((e) => (e.dur ?? 0) / 1000).sort((a, b) => a - b);
  const p95 = durs[Math.floor(durs.length * 0.95)];
  const worst = durs[durs.length - 1];
  console.log(`rAF frames=${raf.length} p50=${durs[Math.floor(durs.length / 2)].toFixed(2)}ms p95=${p95.toFixed(2)}ms max=${worst.toFixed(2)}ms`);
  // Layout forced inside an animation frame callback.
  const layoutsInRaf = events.filter(
    (e) =>
      e.name === 'Layout' &&
      // only layouts forced by app code (Playwright's injected pollers have no script URL)
      JSON.stringify((e.args as { beginData?: { stackTrace?: unknown } })?.beginData?.stackTrace ?? []).includes('/assets/') &&
      raf.some((f) => f.tid === e.tid && e.ts >= f.ts && e.ts <= f.ts + (f.dur ?? 0)),
  );
  for (const l of layoutsInRaf)
    console.log('layout-in-raf', JSON.stringify((l.args as { beginData?: { stackTrace?: unknown } })?.beginData?.stackTrace ?? l.args).slice(0, 400));
  expect(layoutsInRaf.length).toBe(0);
  expect(p95).toBeLessThanOrEqual(4);
  expect(worst).toBeLessThanOrEqual(16.7);
});
