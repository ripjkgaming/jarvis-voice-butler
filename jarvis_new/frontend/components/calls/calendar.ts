/** Calendar activity during a call, for the HUD's CALENDAR panel.
 *
 *  The backend does not emit calendar events on the call stream yet
 *  (docs/calls/UI_NEEDS.md). Until it does, this module reads the
 *  sentences that src/calls/calendar_hook.py speaks after each CHECK, BOOK,
 *  REMIND or CHANGE tool call, plus the matching escalation reasons from
 *  calendar_tools.py. Those sentences are fixed templates, so the match is
 *  exact. When `{type:"call", event:"calendar"}` events arrive (shape in
 *  UI_NEEDS.md), they replace the parsed speech for that call.
 *
 *  Pure: no React, no imports at runtime (tests load it in a vm). */
import type { CalendarStreamEvent, CallView, TranscriptLine } from './types';

export type CalKind = 'check' | 'booking' | 'reminder' | 'change';
export type CalStatus =
  | 'free'
  | 'clash'
  | 'booked'
  | 'pending'
  | 'hold'
  | 'denied'
  | 'found'
  | 'ambiguous'
  | 'near'
  | 'missing'
  | 'withheld'
  | 'escalated';

export type CalSlot = { title: string; when: string };

export type CalItem = {
  id: string;
  kind: CalKind;
  status: CalStatus;
  /** Booking title or the matched event's title. */
  title: string | null;
  /** Display time, `SAT 14 NOV · 19:00`. */
  when: string | null;
  who: string | null;
  /** Existing event that blocks the requested slot. */
  conflict: CalSlot | null;
  /** Suggested free start times, display form. */
  alts: string[];
  /** Other matching or nearby events (reminder lookups). */
  matches: CalSlot[];
  /** The caller's words that led to this lookup. */
  ask: string | null;
  reason: string | null;
  /** epoch ms */
  ts: number;
  /** `stream` = calendar event from the backend; `speech` = parsed reply. */
  source: 'stream' | 'speech';
};

const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

/** `2026-11-14T19:00[...]` → `SAT 14 NOV · 19:00`. Wall-clock fields are
 *  read from the string, so the owner's timezone is kept as sent. */
export function fmtIso(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(iso.trim());
  if (!m) return iso;
  const [, y, mo, d, h, mi] = m;
  const day = new Date(Date.UTC(+y, +mo - 1, +d)).getUTCDay();
  const date = `${DAYS[day]} ${+d} ${MONTHS[+mo - 1]}`;
  return h ? `${date} · ${h}:${mi}` : date;
}

/** calendar_hook `_fmt` output, `Saturday 14 November at 19:00`, to the
 *  same display form as `fmtIso`. */
export function fmtSpoken(s: string): string {
  const m = /^\s*([A-Za-z]+)\s+(\d{1,2})\s+([A-Za-z]+)(?:\s+at\s+(\d{1,2}:\d{2}))?/.exec(s);
  if (!m) return s.trim();
  const [, day, d, mon, hm] = m;
  const date = `${day.slice(0, 3).toUpperCase()} ${+d} ${mon.slice(0, 3).toUpperCase()}`;
  return hm ? `${date} · ${hm.padStart(5, '0')}` : date;
}

const list = (s: string | undefined) =>
  (s ?? '')
    .split(/,\s*/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map(fmtSpoken);

type Hit = Omit<CalItem, 'id' | 'ask' | 'ts' | 'source' | 'reason'> & { reason?: string | null };

const base = (kind: CalKind, status: CalStatus): Hit => ({
  kind,
  status,
  title: null,
  when: null,
  who: null,
  conflict: null,
  alts: [],
  matches: [],
});

/** Template sentences from calendar_hook._run, most specific first. */
const RULES: [RegExp, (m: RegExpExecArray) => Hit][] = [
  [/That slot looks free\./, () => base('check', 'free')],
  [
    /That slot is taken\. Nearby free times: ([^.]*)\./,
    (m) => ({ ...base('check', 'clash'), alts: list(m[1]) }),
  ],
  [
    /Done, (.+?) is in the calendar for ([^.]+)\./,
    (m) => ({ ...base('booking', 'booked'), title: m[1], when: fmtSpoken(m[2]) }),
  ],
  [
    /That time is already taken\.(?: I could offer ([^.]*)\.)?/,
    (m) => ({ ...base('booking', 'clash'), alts: list(m[1]) }),
  ],
  [
    /That time just became unavailable\.\s*([^.]*)/,
    (m) => ({ ...base('booking', 'clash'), alts: list(m[1]) }),
  ],
  [/I can't make bookings for you directly\./, () => base('booking', 'hold')],
  [
    /I can't take that booking: (.+?)\. Could you/,
    (m) => ({ ...base('booking', 'denied'), reason: m[1] }),
  ],
  [
    /I've noted (.+?) for ([^.]+?) as a request\./,
    (m) => ({ ...base('booking', 'pending'), title: m[1], when: fmtSpoken(m[2]) }),
  ],
  [
    /There are two: (.+?) on ([^,]+), and (.+?) on ([^.]+)\. Which one\?/,
    (m) => ({
      ...base('reminder', 'ambiguous'),
      title: m[1],
      when: fmtSpoken(m[2]),
      matches: [{ title: m[3], when: fmtSpoken(m[4]) }],
    }),
  ],
  [
    /Yes, (.+?) (?:is|was) on ([^.]+)\./,
    (m) => ({ ...base('reminder', 'found'), title: m[1], when: fmtSpoken(m[2]) }),
  ],
  [
    /I don't see that exactly\. Something close is (.+?) on ([^.]+)\./,
    (m) => ({ ...base('reminder', 'near'), matches: [{ title: m[1], when: fmtSpoken(m[2]) }] }),
  ],
  [/I don't see that in the calendar\./, () => base('reminder', 'missing')],
  [/I can't share calendar details\./, () => base('reminder', 'withheld')],
  [/I can't change existing events\./, () => base('change', 'escalated')],
];

/** Calendar result spoken in one Jarvis line, or null. */
export function parseCalendarSpeech(text: string): Hit | null {
  for (const [re, make] of RULES) {
    const m = re.exec(text);
    if (m) return make(m);
  }
  return null;
}

/** Short tag for a transcript line that carries a calendar result. */
export const STATUS_TAG: Record<CalStatus, string> = {
  free: 'SLOT FREE',
  clash: 'SLOT CLASH',
  booked: 'BOOKED',
  pending: 'NEEDS APPROVAL',
  hold: 'TENTATIVE HOLD',
  denied: 'NOT BOOKED',
  found: 'IN CALENDAR',
  ambiguous: 'TWO MATCHES',
  near: 'CLOSE MATCH',
  missing: 'NOT IN CALENDAR',
  withheld: 'DETAILS WITHHELD',
  escalated: 'SENT TO OWNER',
};

export function calendarTag(text: string): CalStatus | null {
  return parseCalendarSpeech(text)?.status ?? null;
}

/** `unknown caller wants a booking: Meeting at 2026-11-14T19:00:00+08:00`
 *  and `booking needs owner approval: Dinner at ...` (calendar_tools.py). */
function fromReason(reason: string): { title: string; when: string } | null {
  const m = /(?:wants a booking|needs owner approval): (.+) at (\d{4}-\d{2}-\d{2}T[\d:]+)/.exec(
    reason
  );
  return m ? { title: m[1], when: fmtIso(m[2]) } : null;
}

const STREAM_STATUS: Record<string, CalStatus> = {
  free: 'free',
  busy: 'clash',
  clash: 'clash',
  confirmed: 'booked',
  booked: 'booked',
  proposed: 'pending',
  escalated: 'pending',
  hold_pending_owner: 'hold',
  denied: 'denied',
  found: 'found',
  ambiguous: 'ambiguous',
  near: 'near',
  missing: 'missing',
  withheld: 'withheld',
  change_escalated: 'escalated',
};

function fromStream(ev: CalendarStreamEvent, i: number, ts: number): CalItem {
  const kind: CalKind =
    ev.action === 'book'
      ? 'booking'
      : ev.action === 'remind'
        ? 'reminder'
        : ev.action === 'change'
          ? 'change'
          : 'check';
  let status = STREAM_STATUS[ev.status] ?? 'denied';
  if (ev.status === 'denied' && ev.clashes?.length) status = 'clash';
  const slot = (x: { title?: string; start: string }) => ({
    title: x.title ?? '',
    when: fmtIso(x.start),
  });
  const match = ev.match ? slot(ev.match) : null;
  return {
    id: `s${i}`,
    kind,
    status,
    title: ev.title ?? match?.title ?? null,
    when: ev.start ? fmtIso(ev.start) : (match?.when ?? null),
    who: ev.who ?? null,
    conflict: ev.clashes?.length ? slot(ev.clashes[0]) : null,
    alts: (ev.suggestions ?? []).map((x) => fmtIso(x.start)).slice(0, 3),
    matches: (ev.nearby ?? []).map(slot).slice(0, 3),
    ask: ev.query ?? null,
    reason: ev.reason ?? null,
    ts,
    source: 'stream',
  };
}

/** Every calendar lookup in this call, oldest first. */
export function calendarItems(call: CallView): CalItem[] {
  if (call.calendar.length) return call.calendar.map((c, i) => fromStream(c.ev, i, c.at));
  const out: CalItem[] = [];
  let ask: string | null = null;
  const reasons = call.escalations.map((e) => e.reason);
  call.transcript.forEach((l: TranscriptLine) => {
    if (l.mark || l.partial) return;
    if (l.speaker === 'caller') {
      ask = l.text;
      return;
    }
    if (l.speaker !== 'jarvis') return;
    const hit = parseCalendarSpeech(l.text);
    if (!hit) return;
    const item: CalItem = {
      ...hit,
      reason: hit.reason ?? null,
      id: `t${l.id}`,
      ask,
      ts: l.ts,
      source: 'speech',
    };
    if (hit.status === 'hold' || hit.status === 'pending') {
      const r = reasons.map(fromReason).find(Boolean);
      if (r) {
        item.title = item.title ?? r.title;
        item.when = item.when ?? r.when;
      }
    }
    if (hit.kind === 'change') {
      const r = reasons.find((x) => x.startsWith('caller wants to'));
      if (r) item.reason = r.replace(/^caller wants to /, '');
    }
    out.push(item);
  });
  return out;
}

/** `SAT 14 NOV · 19:00` → minutes after midnight, or null. */
export function minutesOf(when: string | null): number | null {
  const m = when ? /(\d{2}):(\d{2})$/.exec(when) : null;
  return m ? +m[1] * 60 + +m[2] : null;
}
