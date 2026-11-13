'use client';

import { useEffect, useState } from 'react';
import { type HolidayTheme, findHoliday, getHoliday } from '@/lib/holidays';
import { useDataTopic } from '@/lib/voice-session-react';

/** localStorage: a holiday id, "off", or absent = automatic by date. */
export const HOLIDAY_STORAGE_KEY = 'jarvis.holiday';
const REFRESH_MS = 10 * 60 * 1000;

type Override = { mode: 'auto' } | { mode: 'off' } | { mode: 'theme'; theme: HolidayTheme };

/** Parse "<id|alias>", "off"/"none", "auto"/"" -> override; null = unknown. */
function parse(raw: string | null | undefined): Override | null {
  const v = (raw ?? '').trim().toLowerCase();
  if (v === '' || v === 'auto' || v === 'on') return { mode: 'auto' };
  if (v === 'off' || v === 'none' || v === 'false' || v === '0') return { mode: 'off' };
  const theme = findHoliday(v);
  return theme ? { mode: 'theme', theme } : null;
}

function store(value: string): void {
  try {
    if (value === 'auto') localStorage.removeItem(HOLIDAY_STORAGE_KEY);
    else localStorage.setItem(HOLIDAY_STORAGE_KEY, value);
  } catch {
    /* storage blocked: override lasts for this session only */
  }
}

function initial(): Override {
  try {
    const q = new URLSearchParams(window.location.search).get('holiday');
    const fromQuery = q === null ? null : parse(q);
    if (fromQuery) {
      store(fromQuery.mode === 'theme' ? fromQuery.theme.id : fromQuery.mode);
      return fromQuery;
    }
    return parse(localStorage.getItem(HOLIDAY_STORAGE_KEY)) ?? { mode: 'auto' };
  } catch {
    return { mode: 'auto' };
  }
}

/**
 * Active holiday theme. Precedence (last writer wins, all persisted):
 *  1. voice / bridge event: data topic `jarvis-holiday` with
 *     `{ holiday: "<id>|off|auto" }`, or window event `jarvis-holiday`
 *     (`detail` = the same string);
 *  2. `?holiday=<id|off|auto>` in the URL;
 *  3. localStorage `jarvis.holiday`;
 *  4. the calendar (lib/holidays.ts, Asia/Singapore).
 */
export function useHoliday(): HolidayTheme | null {
  const [override, setOverride] = useState<Override>({ mode: 'auto' });
  const [auto, setAuto] = useState<HolidayTheme | null>(null);

  useEffect(() => {
    setOverride(initial());
    const tick = () => setAuto(getHoliday(new Date()));
    tick();
    const t = setInterval(tick, REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  const apply = (raw: unknown) => {
    const text =
      typeof raw === 'string'
        ? raw
        : raw && typeof raw === 'object' && 'holiday' in raw
          ? String((raw as { holiday: unknown }).holiday ?? '')
          : null;
    if (text === null) return;
    const next = parse(text);
    if (!next) return;
    store(next.mode === 'theme' ? next.theme.id : next.mode);
    setOverride(next);
  };

  useDataTopic('jarvis-holiday', apply);
  useEffect(() => {
    const onEvent = (e: Event) => apply((e as CustomEvent).detail);
    window.addEventListener('jarvis-holiday', onEvent);
    return () => window.removeEventListener('jarvis-holiday', onEvent);
    // apply only touches state setters + storage.
  }, []);

  if (override.mode === 'off') return null;
  if (override.mode === 'theme') return override.theme;
  return auto;
}
