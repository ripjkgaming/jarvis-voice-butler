/** Jarvis Calls: UI-side types for the call event stream
 *  (docs/calls/CONTRACT.md, "Call event stream"). The agent (src/calls/events.py)
 *  emits these as `{type: "call", ...}` over the HUD data channel. */

export type CallStateName = 'ringing' | 'handling' | 'hold' | 'owner' | 'ended';
export type CallDirection = 'in' | 'out';
export type Trust = 'family' | 'work' | 'unknown' | 'blocked';
export type Speaker = 'caller' | 'jarvis' | 'owner';
export type CallMode = 'jarvis' | 'owner_voice';

export type Caller = {
  name: string;
  number: string;
  trust: Trust;
  relationship: string;
  notes: string;
};

export type CallStateEvent = {
  type: 'call';
  event: 'state';
  call_id: string;
  state: CallStateName;
  direction: CallDirection;
  caller: Caller | null;
  goal: string;
  started_at: number;
  mode: CallMode;
  ai_disclosed: boolean;
  recording: boolean;
};

export type CallTranscriptEvent = {
  type: 'call';
  event: 'transcript';
  call_id: string;
  speaker: Speaker;
  text: string;
  partial: boolean;
  ts: number;
};

export type CallSpeakingEvent = { type: 'call'; event: 'speaking'; call_id: string; level: number };
export type CallEscalationEvent = {
  type: 'call';
  event: 'escalation';
  call_id: string;
  reason: string;
};
export type CallSummaryEvent = {
  type: 'call';
  event: 'summary';
  call_id: string;
  summary: string;
  outcome: string;
};

/** Proposed, not in the contract yet (docs/calls/UI_NEEDS.md): one
 *  calendar tool result. The HUD falls back to parsing Jarvis's spoken
 *  result until the backend emits it. */
export type CalendarStreamEvent = {
  type: 'call';
  event: 'calendar';
  call_id: string;
  action: 'check' | 'book' | 'remind' | 'change';
  status: string;
  title?: string;
  start?: string;
  end?: string;
  who?: string;
  query?: string;
  reason?: string;
  match?: { title: string; start: string; end?: string } | null;
  clashes?: { title: string; start: string; end?: string }[];
  suggestions?: { start: string; end?: string }[];
  nearby?: { title: string; start: string; end?: string }[];
};

export type CallEvent =
  | CallStateEvent
  | CallTranscriptEvent
  | CallSpeakingEvent
  | CallEscalationEvent
  | CallSummaryEvent
  | CalendarStreamEvent;

export type TranscriptLine = {
  id: number;
  speaker: Speaker;
  text: string;
  partial: boolean;
  /** epoch ms */
  ts: number;
  /** HUD-made event marker (state change), not speech. */
  mark?: boolean;
};

/** Reduced view of one call, everything the HUD renders except the
 *  speaking level (kept in a ref so 20 Hz level packets never re-render). */
export type CallView = {
  callId: string;
  state: CallStateName;
  direction: CallDirection;
  caller: Caller | null;
  goal: string;
  /** epoch ms */
  startedAt: number;
  /** epoch ms when `state` last changed (hold timer etc.) */
  stateSince: number;
  /** epoch ms when the call ended, else null (freezes the call timer) */
  endedAt: number | null;
  mode: CallMode;
  aiDisclosed: boolean;
  recording: boolean;
  transcript: TranscriptLine[];
  escalation: { reason: string; at: number } | null;
  /** Every escalation this call, also after it resolved (calendar panel). */
  escalations: { reason: string; at: number }[];
  /** Backend calendar events, if the stream carries them. */
  calendar: { ev: CalendarStreamEvent; at: number }[];
  summary: { summary: string; outcome: string } | null;
};

/** Optional side data. Not in the event stream contract: a bridge
 *  endpoint (or the demo) supplies it. */
export type CallRule = {
  id: string | number;
  kind: 'answer_all' | 'answer_unknown' | 'reject' | 'message_only';
  window: string;
  active: boolean;
  note?: string;
};
export type ScheduledCall = { at: string; who: string; goal: string };
export type QuickContact = { name: string; trust: Trust; relationship: string; last?: string };
