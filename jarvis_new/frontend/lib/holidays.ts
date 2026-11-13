/** Holiday themes for the HUD decor overlay (components/ascii/holiday-decor.tsx).
 *  Pure: no DOM, no clock reads. `getHoliday(date)` resolves the active theme
 *  from calendar day in a fixed timezone (default Asia/Singapore).
 *  Lunar / Islamic / Buddhist / Hindu dates are tables for 2026-2030 (SG
 *  gazette dates; Islamic + Vesak can shift +-1 day with moon sighting). */

export type ParticleKind =
  | 'snow'
  | 'lanterns'
  | 'fireworks'
  | 'confetti'
  | 'bats'
  | 'leaves'
  | 'hearts'
  | 'diyas'
  | 'stars';

export type HolidayTheme = {
  id: string;
  name: string;
  /** Extra ids accepted by `?holiday=` and the voice event. */
  aliases: string[];
  /** Accent tint: "r, g, b" (for rgba(var(--holiday-acc), a)) and hot hex. */
  tint: { acc: string; hot: string };
  /** Particle colours (hex), drawn from at random. */
  colors: string[];
  /** Particle glyphs, drawn from at random. */
  glyphs: string[];
  particle: ParticleKind;
  /** 0.5 sparse .. 1.5 dense. */
  density: number;
  /** Corner ornament, ASCII art lines. */
  ornament: string[];
  banner?: string;
};

export const HOLIDAYS: Record<string, HolidayTheme> = {
  'new-year': {
    id: 'new-year',
    name: 'New Year',
    aliases: ['newyear', 'ny', 'new-years'],
    tint: { acc: '255, 215, 120', hot: '#fff3c4' },
    colors: ['#ffd778', '#5fe3ff', '#ffffff', '#ffb347'],
    glyphs: ['*', '+', '·', '✦'],
    particle: 'fireworks',
    density: 1,
    ornament: [' \\ | / ', '-- * --', ' / | \\ '],
    banner: 'HAPPY NEW YEAR',
  },
  cny: {
    id: 'cny',
    name: 'Chinese New Year',
    aliases: ['lny', 'lunar-new-year', 'chinese-new-year', 'cny'],
    tint: { acc: '255, 77, 94', hot: '#ffd2a8' },
    colors: ['#ff4d5e', '#ffd778', '#ff8a3d'],
    glyphs: ['◉', '◎', '福', '春'],
    particle: 'lanterns',
    density: 1,
    ornament: ['  ┃  ', '╭─┻─╮', '│福 │', '╰─┳─╯'],
    banner: '新年快乐 · GONG XI FA CAI',
  },
  valentines: {
    id: 'valentines',
    name: "Valentine's Day",
    aliases: ['valentine', 'valentines-day', 'love'],
    tint: { acc: '255, 120, 170', hot: '#ffd6e6' },
    colors: ['#ff78aa', '#ff4d5e', '#ffd6e6'],
    glyphs: ['♥', '♡', '❤'],
    particle: 'hearts',
    density: 0.8,
    ornament: [' ♥♥ ♥♥ ', '♥♥♥♥♥♥♥', ' ♥♥♥♥♥ ', '  ♥♥♥  ', '   ♥   '],
    banner: "HAPPY VALENTINE'S",
  },
  easter: {
    id: 'easter',
    name: 'Easter',
    aliases: [],
    tint: { acc: '190, 160, 255', hot: '#f0e6ff' },
    colors: ['#c9a8ff', '#ffe08a', '#8affc1', '#ff9ec4'],
    glyphs: ['o', '0', '◍', '○'],
    particle: 'confetti',
    density: 0.7,
    ornament: ['  ,-.  ', ' / ~ \\ ', '(  ≈  )', ' `---´ '],
    banner: 'HAPPY EASTER',
  },
  'hari-raya-puasa': {
    id: 'hari-raya-puasa',
    name: 'Hari Raya Puasa',
    aliases: ['eid', 'eid-al-fitr', 'hari-raya', 'puasa', 'raya'],
    tint: { acc: '109, 255, 179', hot: '#d8ffe9' },
    colors: ['#6dffb3', '#ffd778', '#ffffff'],
    glyphs: ['☾', '✦', '·', '*'],
    particle: 'stars',
    density: 0.9,
    ornament: ['  ▄▀▀▄ ', ' █ ✦   ', ' █      ', '  ▀▄▄▀ '],
    banner: 'SELAMAT HARI RAYA',
  },
  'hari-raya-haji': {
    id: 'hari-raya-haji',
    name: 'Hari Raya Haji',
    aliases: ['eid-al-adha', 'haji', 'adha'],
    tint: { acc: '109, 255, 179', hot: '#d8ffe9' },
    colors: ['#6dffb3', '#ffd778', '#ffffff'],
    glyphs: ['☾', '✦', '·', '*'],
    particle: 'stars',
    density: 0.7,
    ornament: ['  ▄▀▀▄ ', ' █ ✦   ', ' █      ', '  ▀▄▄▀ '],
    banner: 'SELAMAT HARI RAYA HAJI',
  },
  vesak: {
    id: 'vesak',
    name: 'Vesak Day',
    aliases: ['wesak', 'buddha'],
    tint: { acc: '255, 200, 90', hot: '#fff0c8' },
    colors: ['#ffc85a', '#ffffff', '#ffe9b0'],
    glyphs: ['◎', '○', '✿', '·'],
    particle: 'lanterns',
    density: 0.7,
    ornament: ['  ,▲,  ', ' (✿✿✿) ', '  `▼´  '],
    banner: 'HAPPY VESAK DAY',
  },
  'national-day': {
    id: 'national-day',
    name: 'SG National Day',
    aliases: ['sg', 'nd', 'singapore', 'sg-national-day'],
    tint: { acc: '255, 77, 94', hot: '#ffffff' },
    colors: ['#ff4d5e', '#ffffff', '#ff8a96'],
    glyphs: ['*', '+', '☾', '★'],
    particle: 'fireworks',
    density: 1.1,
    ornament: [' ☾ ★★ ', '   ★★ ', '   ★  '],
    banner: 'MAJULAH SINGAPURA',
  },
  'mid-autumn': {
    id: 'mid-autumn',
    name: 'Mid-Autumn Festival',
    aliases: ['mooncake', 'lantern-festival'],
    tint: { acc: '255, 179, 71', hot: '#ffe6c0' },
    colors: ['#ffb347', '#ffd778', '#ff8a3d'],
    glyphs: ['◉', '◎', '○', '月'],
    particle: 'lanterns',
    density: 0.8,
    ornament: ['  ___  ', ' / ◔ \\ ', ' \\___/ '],
    banner: 'HAPPY MID-AUTUMN',
  },
  deepavali: {
    id: 'deepavali',
    name: 'Deepavali',
    aliases: ['diwali', 'divali'],
    tint: { acc: '255, 179, 71', hot: '#fff0c0' },
    colors: ['#ffb347', '#ffd778', '#ff6a3d', '#ffe9a0'],
    glyphs: ['·', '*', '✦', '▴'],
    particle: 'diyas',
    density: 1,
    ornament: ['  ) (  ', '  (▲)  ', ' \\___/ '],
    banner: 'HAPPY DEEPAVALI · SHUBH DIWALI',
  },
  holi: {
    id: 'holi',
    name: 'Holi',
    aliases: [],
    tint: { acc: '255, 120, 200', hot: '#ffffff' },
    colors: ['#ff78c8', '#ffd778', '#6dffb3', '#5fe3ff', '#b388ff'],
    glyphs: ['▪', '◆', '●', '░'],
    particle: 'confetti',
    density: 1.3,
    ornament: ['░▒▓█▓▒░', ' ▒▓█▓▒ ', '  ▓█▓  '],
    banner: 'HAPPY HOLI',
  },
  halloween: {
    id: 'halloween',
    name: 'Halloween',
    aliases: ['spooky'],
    tint: { acc: '255, 140, 50', hot: '#ffd2a0' },
    colors: ['#ff8c32', '#b388ff', '#ffd778'],
    glyphs: ['^v^', '~v~', '^w^'],
    particle: 'bats',
    density: 0.6,
    ornament: ['  /\\  /\\ ', ' (  ()  )', '  \\_vv_/ '],
    banner: 'HAPPY HALLOWEEN',
  },
  thanksgiving: {
    id: 'thanksgiving',
    name: 'Thanksgiving',
    aliases: [],
    tint: { acc: '255, 160, 70', hot: '#ffe0b8' },
    colors: ['#ff8c32', '#d9622b', '#ffd778', '#b8742f'],
    glyphs: ['%', '&', '§', '¶'],
    particle: 'leaves',
    density: 0.8,
    ornament: ['  .~~.  ', ' (    ) ', '  `--´  '],
    banner: 'HAPPY THANKSGIVING',
  },
  christmas: {
    id: 'christmas',
    name: 'Christmas',
    aliases: ['xmas'],
    tint: { acc: '109, 255, 179', hot: '#ffffff' },
    colors: ['#ffffff', '#d8f6ff', '#6dffb3', '#ff4d5e'],
    glyphs: ['*', '·', '❄', '+'],
    particle: 'snow',
    density: 1.1,
    ornament: ['   *   ', '  /o\\  ', ' /_o_\\ ', '   |   '],
    banner: 'MERRY CHRISTMAS',
  },
};

/** Resolve an id, alias or loose spelling (case, spaces, underscores). */
export function findHoliday(key: string | null | undefined): HolidayTheme | null {
  if (!key) return null;
  const k = key
    .trim()
    .toLowerCase()
    .replace(/[\s_]+/g, '-');
  if (HOLIDAYS[k]) return HOLIDAYS[k];
  for (const t of Object.values(HOLIDAYS)) if (t.aliases.includes(k)) return t;
  return null;
}

/* ------------------------------------------------------------ calendar */

/** Days since epoch for a calendar day (month 1-12). */
const day = (y: number, m: number, d: number) => Math.round(Date.UTC(y, m - 1, d) / 86400000);

/** Gregorian Easter Sunday (anonymous / Meeus computus). */
export function easterDay(y: number): number {
  const a = y % 19;
  const b = Math.floor(y / 100);
  const c = y % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  return day(y, month, ((h + l - 7 * m + 114) % 31) + 1);
}

/** 4th Thursday of November. */
function thanksgivingDay(y: number): number {
  const first = day(y, 11, 1);
  // Epoch day 0 (1970-01-01) is a Thursday.
  return first + ((7 - (first % 7)) % 7) + 21;
}

type Table = Record<number, [number, number]>; // year -> [month, day]
const fromTable = (t: Table) => (y: number) => (t[y] ? day(y, t[y][0], t[y][1]) : null);

const CNY = fromTable({
  2026: [2, 17],
  2027: [2, 6],
  2028: [1, 26],
  2029: [2, 13],
  2030: [2, 3],
});
const PUASA = fromTable({
  2026: [3, 21],
  2027: [3, 10],
  2028: [2, 27],
  2029: [2, 14],
  2030: [2, 4],
});
const HAJI = fromTable({
  2026: [5, 27],
  2027: [5, 17],
  2028: [5, 5],
  2029: [4, 24],
  2030: [4, 13],
});
const VESAK = fromTable({
  2026: [5, 31],
  2027: [5, 20],
  2028: [5, 9],
  2029: [5, 27],
  2030: [5, 16],
});
const DEEPAVALI = fromTable({
  2026: [11, 8],
  2027: [10, 29],
  2028: [10, 17],
  2029: [11, 5],
  2030: [10, 26],
});
const HOLI = fromTable({
  2026: [3, 4],
  2027: [3, 22],
  2028: [3, 11],
  2029: [3, 1],
  2030: [3, 20],
});
const MID_AUTUMN = fromTable({
  2026: [9, 25],
  2027: [9, 15],
  2028: [10, 3],
  2029: [9, 22],
  2030: [9, 12],
});

type Rule = {
  id: string;
  /** Holiday day for calendar year y, or null when outside the table. */
  at: (y: number) => number | null;
  before: number;
  after: number;
  /** Tie-break when windows overlap at equal distance: higher wins. */
  rank: number;
};

const fixed = (m: number, d: number) => (y: number) => day(y, m, d);

const RULES: Rule[] = [
  { id: 'new-year', at: fixed(1, 1), before: 1, after: 1, rank: 5 },
  { id: 'cny', at: CNY, before: 7, after: 15, rank: 9 },
  { id: 'valentines', at: fixed(2, 14), before: 3, after: 0, rank: 4 },
  { id: 'holi', at: HOLI, before: 2, after: 1, rank: 4 },
  { id: 'easter', at: easterDay, before: 3, after: 1, rank: 4 },
  { id: 'hari-raya-puasa', at: PUASA, before: 5, after: 3, rank: 8 },
  { id: 'hari-raya-haji', at: HAJI, before: 3, after: 1, rank: 8 },
  { id: 'vesak', at: VESAK, before: 2, after: 0, rank: 8 },
  { id: 'national-day', at: fixed(8, 9), before: 9, after: 1, rank: 9 },
  { id: 'mid-autumn', at: MID_AUTUMN, before: 3, after: 0, rank: 6 },
  { id: 'halloween', at: fixed(10, 31), before: 7, after: 0, rank: 5 },
  { id: 'deepavali', at: DEEPAVALI, before: 5, after: 2, rank: 9 },
  { id: 'thanksgiving', at: thanksgivingDay, before: 2, after: 0, rank: 3 },
  { id: 'christmas', at: fixed(12, 25), before: 7, after: 1, rank: 6 },
];

/** Calendar day (epoch days) of `date` in `tz`. */
function localDay(date: Date, tz: string): number {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  return day(get('year'), get('month'), get('day'));
}

/** Active holiday theme for `date` (a day inside any window), else null.
 *  Overlaps: closest to the holiday itself wins, then higher rank. */
export function getHoliday(date: Date = new Date(), tz = 'Asia/Singapore'): HolidayTheme | null {
  if (Number.isNaN(date.getTime())) return null;
  const today = localDay(date, tz);
  const year = new Date(today * 86400000).getUTCFullYear();
  let best: { rule: Rule; dist: number } | null = null;
  for (const rule of RULES) {
    for (const y of [year - 1, year, year + 1]) {
      const at = rule.at(y);
      if (at === null) continue;
      const off = today - at; // negative = before the day
      if (off < -rule.before || off > rule.after) continue;
      const dist = Math.abs(off);
      if (!best || dist < best.dist || (dist === best.dist && rule.rank > best.rule.rank)) {
        best = { rule, dist };
      }
    }
  }
  return best ? HOLIDAYS[best.rule.id] : null;
}
