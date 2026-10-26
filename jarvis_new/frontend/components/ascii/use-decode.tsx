'use client';

import { useEffect, useState } from 'react';
import { prefersReducedMotion, scramble } from '@/lib/ascii';

/** Text that decodes out of scrambled glyphs whenever it changes. */
export function useDecode(text: string, ms = 420, delay = 0) {
  const [out, setOut] = useState(text);
  useEffect(() => {
    if (prefersReducedMotion() || !text) {
      setOut(text);
      return;
    }
    const t0 = performance.now() + delay;
    let raf = 0;
    const step = (now: number) => {
      const p = Math.max(0, (now - t0) / ms);
      setOut(p >= 1 ? text : scramble(text, p, Math.floor(now / 50)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [text, ms, delay]);
  return out;
}

export function Decode({ text, ms, delay }: { text: string; ms?: number; delay?: number }) {
  return <>{useDecode(text, ms, delay)}</>;
}
