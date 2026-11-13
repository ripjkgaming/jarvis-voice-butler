// Run from frontend: node --test tests/call-calendar.test.cjs
// Calendar panel data: spoken calendar_hook results and bridge feed helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function load(file, extra = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../components/calls', file), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const scope = { exports: {}, require: () => ({}), Math, Number, String, Date, ...extra };
  vm.runInNewContext(compiled, scope, { filename: file });
  return scope.exports;
}

const cal = load('calendar.ts');
const red = load('call-reducer.ts');

const view = (lines, extra = {}) => ({
  callId: 'c1',
  transcript: lines.map(([speaker, text], i) => ({
    id: i + 1,
    speaker,
    text,
    partial: false,
    ts: 1000 + i,
  })),
  escalations: [],
  calendar: [],
  ...extra,
});

test('dates format the same from ISO and from speech', () => {
  assert.equal(cal.fmtIso('2026-11-14T19:00:00+08:00'), 'SAT 14 NOV · 19:00');
  assert.equal(cal.fmtSpoken('Saturday 14 November at 19:00'), 'SAT 14 NOV · 19:00');
  assert.equal(cal.fmtSpoken('Saturday 14 November at 9:30'), 'SAT 14 NOV · 09:30');
  assert.equal(cal.minutesOf('SAT 14 NOV · 19:30'), 19 * 60 + 30);
});

test('booking confirmed', () => {
  const [it] = cal.calendarItems(
    view([
      ['caller', 'Can you book dinner with me tomorrow at 7pm?'],
      [
        'jarvis',
        'Let me do that. Done, Dinner is in the calendar for Saturday 14 November at 19:00.',
      ],
    ])
  );
  assert.equal(it.kind, 'booking');
  assert.equal(it.status, 'booked');
  assert.equal(it.title, 'Dinner');
  assert.equal(it.when, 'SAT 14 NOV · 19:00');
  assert.equal(it.ask, 'Can you book dinner with me tomorrow at 7pm?');
});

test('booking clash lists alternatives', () => {
  const [it] = cal.calendarItems(
    view([
      [
        'jarvis',
        'That time is already taken. I could offer Saturday 14 November at 18:30, Saturday 14 November at 20:00, Saturday 14 November at 18:00.',
      ],
    ])
  );
  assert.equal(it.status, 'clash');
  assert.deepEqual(
    [...it.alts],
    ['SAT 14 NOV · 18:30', 'SAT 14 NOV · 20:00', 'SAT 14 NOV · 18:00']
  );
  const [none] = cal.calendarItems(
    view([['jarvis', 'That time is already taken. I have no nearby free slots.']])
  );
  assert.equal(none.status, 'clash');
  assert.equal(none.alts.length, 0);
});

test('reminder found, missing, near, two matches, withheld', () => {
  const st = (t) => cal.calendarItems(view([['jarvis', t]]))[0];
  const f = st('Yes, Dentist appointment is on Saturday 14 November at 15:00.');
  assert.equal(f.status, 'found');
  assert.equal(f.title, 'Dentist appointment');
  assert.equal(
    st("I don't see that in the calendar. I'll let the owner know you asked.").status,
    'missing'
  );
  const n = st(
    "I don't see that exactly. Something close is Dentist checkup on Monday 16 November at 10:00. It isn't in the calendar as asked."
  );
  assert.equal(n.status, 'near');
  assert.equal(n.matches[0].title, 'Dentist checkup');
  const a = st(
    'There are two: Yoga on Saturday 14 November at 08:00, and Yoga class on Sunday 15 November at 09:00. Which one?'
  );
  assert.equal(a.status, 'ambiguous');
  assert.equal(a.matches[0].when, 'SUN 15 NOV · 09:00');
  assert.equal(
    st("I can't share calendar details. I'll pass your question to the owner.").status,
    'withheld'
  );
});

test('unknown caller hold takes title and time from the escalation', () => {
  const [it] = cal.calendarItems(
    view(
      [
        [
          'jarvis',
          "I can't make bookings for you directly. I've noted the request as a tentative hold and the owner will confirm.",
        ],
      ],
      {
        escalations: [
          { reason: 'unknown caller wants a booking: Meeting at 2026-11-14T19:00:00+08:00', at: 1 },
        ],
      }
    )
  );
  assert.equal(it.status, 'hold');
  assert.equal(it.title, 'Meeting');
  assert.equal(it.when, 'SAT 14 NOV · 19:00');
});

test('plain speech is not a calendar result', () => {
  assert.equal(
    cal.calendarItems(view([['jarvis', 'Hello, this is Jarvis, an AI assistant.']])).length,
    0
  );
  assert.equal(cal.calendarTag('That slot looks free.'), 'free');
});

test('stream calendar events replace parsed speech', () => {
  let v = null;
  const fold = (e) => (v = red.reduceCall(v, red.parseCallEvent(e), 5));
  fold({
    type: 'call',
    event: 'state',
    call_id: 1,
    state: 'handling',
    direction: 'in',
    started_at: 1,
  });
  fold({
    type: 'call',
    event: 'transcript',
    call_id: 1,
    speaker: 'jarvis',
    text: 'That time is already taken.',
    partial: false,
    ts: 2,
  });
  fold({
    type: 'call',
    event: 'calendar',
    call_id: 1,
    action: 'book',
    status: 'denied',
    title: 'Dinner',
    start: '2026-11-14T19:00',
    clashes: [{ title: 'Team dinner', start: '2026-11-14T19:00' }],
    suggestions: [{ start: '2026-11-14T18:00' }],
  });
  fold({ type: 'call', event: 'escalation', call_id: 1, reason: 'x' });
  fold({
    type: 'call',
    event: 'state',
    call_id: 1,
    state: 'owner',
    direction: 'in',
    started_at: 1,
  });
  assert.equal(v.escalations.length, 1, 'history survives the state change');
  const items = cal.calendarItems(v);
  assert.equal(items.length, 1);
  assert.equal(items[0].source, 'stream');
  assert.equal(items[0].status, 'clash');
  assert.equal(items[0].conflict.title, 'Team dinner');
  assert.deepEqual([...items[0].alts], ['SAT 14 NOV · 18:00']);
});

test('bridge feed: snapshot order and foreign-clock retiming', () => {
  const feed = load('bridge-feed.ts');
  const st = { event: 'state', call_id: 'a', started_at: 100 };
  const tr = { event: 'transcript', call_id: 'a', ts: 105 };
  const esc = { event: 'escalation', call_id: 'a', reason: 'x' };
  const evs = feed.snapshotEvents({ active: st, transcript: [tr], escalation: esc, next: 9 });
  assert.deepEqual(
    [...evs].map((e) => e.event),
    ['state', 'transcript', 'escalation']
  );
  assert.equal(feed.snapshotEvents({ active: null, transcript: [tr], next: 1 }).length, 0);

  const now = 1_800_000_000_000;
  const shift = feed.makeRetimer(() => now);
  // Sim clock 12 h behind: newest time lands on "now".
  const sim = shift([
    { event: 'state', call_id: 's', started_at: (now - 43_200_000 - 5000) / 1000 },
    { event: 'transcript', call_id: 's', ts: (now - 43_200_000) / 1000 },
  ]);
  assert.equal(sim[1].ts, now);
  assert.equal(sim[0].started_at, now - 5000);
  // Real clock: untouched.
  const real = shift([{ event: 'transcript', call_id: 'r', ts: (now - 2000) / 1000 }]);
  assert.equal(real[0].ts, (now - 2000) / 1000);
});
