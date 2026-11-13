/** Scripted mock calls for app/calls-demo: contract-shaped events
 *  (docs/calls/CONTRACT.md) on a millisecond timeline. Demo data only. */
import type { CallRule, Caller, QuickContact, ScheduledCall } from './types';

/** Fixed demo epoch so frozen screenshots are deterministic. */
export const T0 = Date.UTC(2026, 10, 13, 19, 42, 0);
const sec = (ms: number) => (T0 + ms) / 1000;

export type Cue = { at: number; ev?: Record<string, unknown>; talk?: boolean };

const PRIYA: Caller = {
  name: 'Priya Raman',
  number: '+447700900123',
  trust: 'work',
  relationship: 'Project lead · Halcyon Labs',
  notes:
    'Prefers calls before 18:00\nWaiting on Q4 render budget sign-off\nLast call 3 days ago: moved the design review to Thursday\nStanding order: never commit budget without the owner',
};

const DENTAL: Caller = {
  name: 'Northgate Dental',
  number: '+442079460311',
  trust: 'unknown',
  relationship: 'Dentist · reception desk',
  notes: 'Cleaning booked Tue 18 Nov, 09:30\nReception asked for 24 h notice to move',
};

function stateEv(
  id: string,
  state: string,
  caller: Caller,
  extra: Partial<Record<string, unknown>> = {}
) {
  return {
    type: 'call',
    event: 'state',
    call_id: id,
    state,
    direction: 'in',
    caller,
    goal: 'Find out what Priya needs; book a slot if she wants a review.',
    started_at: sec(0),
    mode: 'jarvis',
    ai_disclosed: state !== 'ringing',
    recording: state !== 'ringing',
    ...extra,
  };
}

function say(id: string, at: number, speaker: string, text: string, dur = 1800): Cue[] {
  // stream the line as three partials, then the final
  const words = text.split(' ');
  const cut = (f: number) => words.slice(0, Math.max(1, Math.round(words.length * f))).join(' ');
  const t = (k: number) => at + Math.round((dur * k) / 4);
  const mk = (txt: string, partial: boolean, when: number) => ({
    at: when,
    ev: {
      type: 'call',
      event: 'transcript',
      call_id: id,
      speaker,
      text: txt,
      partial,
      ts: sec(at),
    },
  });
  const cues: Cue[] = [
    mk(cut(0.3), true, t(0)),
    mk(cut(0.6), true, t(1)),
    mk(cut(0.85), true, t(2)),
  ];
  cues.push(mk(text, false, t(4)));
  if (speaker === 'jarvis') cues.push({ at: t(0), talk: true }, { at: t(4), talk: false });
  return cues;
}

const ID = 'C-2611-0047';

/** Inbound: ring → Jarvis handles → escalation → hold → owner → ended. */
export const INBOUND: Cue[] = [
  { at: 0, ev: stateEv(ID, 'ringing', PRIYA) },
  { at: 7000, ev: stateEv(ID, 'handling', PRIYA) },
  ...say(
    ID,
    7400,
    'jarvis',
    "Good evening, Priya. You've reached Jarvis, an AI assistant answering for Mr. Locke. This call is transcribed. How can I help?",
    4200
  ),
  ...say(
    ID,
    12000,
    'caller',
    'Oh, hi Jarvis. Is he around? I need the render budget signed off today.',
    3000
  ),
  ...say(
    ID,
    15400,
    'jarvis',
    "He's away from the desk. I can pass on the details, or check whether he can take it now.",
    3200
  ),
  ...say(
    ID,
    19000,
    'caller',
    "It's four thousand eight hundred for the Q4 renders. Can you just confirm it for him?",
    3200
  ),
  {
    at: 22600,
    ev: {
      type: 'call',
      event: 'escalation',
      call_id: ID,
      reason:
        'Priya asks you to approve the £4,800 Q4 render budget today. Committing spend needs your approval.',
    },
  },
  ...say(
    ID,
    23000,
    'jarvis',
    "I can't approve spending on his behalf. One moment, I'm checking with him now.",
    3000
  ),
  { at: 30000, ev: stateEv(ID, 'hold', PRIYA) },
  { at: 38000, ev: stateEv(ID, 'owner', PRIYA) },
  ...say(
    ID,
    38400,
    'owner',
    "Priya, hi, it's me. Send the breakdown over and I'll sign it tonight.",
    3000
  ),
  ...say(ID, 41800, 'caller', 'Perfect, sending it now. Thanks!', 1800),
  { at: 45000, ev: stateEv(ID, 'ended', PRIYA) },
  {
    at: 45200,
    ev: {
      type: 'call',
      event: 'summary',
      call_id: ID,
      outcome: 'owner_resolved',
      summary:
        'Priya called for sign-off on the £4,800 Q4 render budget. Jarvis disclosed it was an AI, declined to commit spend and escalated. You took over and promised to sign tonight once she sends the breakdown.',
    },
  },
];

/** Outbound: Jarvis dials the dentist. */
export const OUTBOUND: Cue[] = [
  {
    at: 0,
    ev: stateEv('C-2611-0048', 'ringing', DENTAL, {
      direction: 'out',
      goal: 'Move Tuesday’s cleaning to any afternoon next week.',
      ai_disclosed: false,
      recording: false,
    }),
  },
];

/** Where each frozen ?state= lands on the inbound timeline (ms). */
export const FREEZE: Record<string, { script: 'in' | 'out'; at: number; talk: boolean }> = {
  ringing: { script: 'in', at: 5200, talk: false },
  dialing: { script: 'out', at: 4100, talk: false },
  handling: { script: 'in', at: 17200, talk: true },
  escalation: { script: 'in', at: 25300, talk: true },
  hold: { script: 'in', at: 34200, talk: false },
  owner: { script: 'in', at: 42400, talk: false },
  ended: { script: 'in', at: 52000, talk: false },
};

export const DEMO_RULES: CallRule[] = [
  { id: 1, kind: 'answer_unknown', window: '22:00–07:00 daily', active: false },
  { id: 2, kind: 'answer_all', window: 'until 21:00 today', active: true, note: 'deep work' },
  { id: 3, kind: 'reject', window: 'spam list', active: true },
];

export const DEMO_NEXT: ScheduledCall = {
  at: 'FRI 10:30',
  who: 'Northgate Dental',
  goal: 'move cleaning to next week',
};

export const DEMO_CONTACTS: QuickContact[] = [
  { name: 'Amma', trust: 'family', relationship: 'mother', last: '2 d' },
  { name: 'Priya Raman', trust: 'work', relationship: 'Halcyon Labs', last: '3 d' },
  { name: 'Dev', trust: 'family', relationship: 'brother', last: '1 w' },
  { name: 'Northgate Dental', trust: 'unknown', relationship: 'dentist', last: '5 w' },
  { name: '+44 7911 ••• 552', trust: 'blocked', relationship: 'spam · x4', last: 'today' },
];
