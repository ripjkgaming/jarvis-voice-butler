'use client';

import {
  type CSSProperties,
  type MutableRefObject,
  memo,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { useDecode } from '@/components/ascii/use-decode';
import { barParts, hash, scramble } from '@/lib/ascii';
import {
  type CalItem,
  type CalStatus,
  STATUS_TAG,
  calendarItems,
  calendarTag,
  fmtIso,
  minutesOf,
} from './calendar';
import { CallReactor, CallWave, type ReactorMood } from './call-reactor';
import s from './calls.module.css';
import type {
  CallRule,
  CallView,
  QuickContact,
  ScheduledCall,
  Speaker,
  TranscriptLine,
  Trust,
} from './types';

/* ------------------------------------------------------------ vocabulary */

/** Spoken phrases for the agent's voice commands (CONTRACT.md:
 *  events.py handle_command). The HUD only shows them; it sends nothing. */
export const VOICE: Record<string, [phrase: string, effect: string]> = {
  accept: ['ANSWER IT', 'jarvis picks up'],
  decline: ['DECLINE', 'reject the call'],
  take_message: ['TAKE A MESSAGE', 'jarvis records a note'],
  takeover: ['TAKE OVER', 'you are on the line'],
  hold: ['HOLD', 'caller waits'],
  resume: ['RESUME', 'jarvis takes the line'],
  hangup: ['HANG UP', 'end the call'],
  stop_all: ['STOP ALL CALL RULES', 'cancel auto-answer'],
};

type Phase = 'ringing' | 'dialing' | 'handling' | 'hold' | 'owner' | 'escalation' | 'ended';

function phaseOf(c: CallView): Phase {
  if (c.state === 'ringing') return c.direction === 'out' ? 'dialing' : 'ringing';
  if (c.escalation && c.state !== 'ended') return 'escalation';
  return c.state;
}

const HINTS: Record<Phase, string[]> = {
  ringing: ['accept', 'takeover', 'take_message', 'decline'],
  dialing: ['takeover', 'hangup'],
  handling: ['takeover', 'hold', 'take_message', 'hangup'],
  hold: ['resume', 'takeover', 'hangup'],
  owner: ['resume', 'hold', 'hangup'],
  escalation: ['takeover', 'take_message', 'hangup'],
  ended: [],
};

/** Accent per phase: `--acc` rgb triple + the reactor's glyph tint. */
const TONE: Record<Phase, [rgb: string, hex: string]> = {
  ringing: ['168, 235, 255', '#a8ebff'],
  dialing: ['168, 235, 255', '#a8ebff'],
  handling: ['168, 235, 255', '#22d3ee'],
  hold: ['147, 184, 202', '#7fa6bb'],
  owner: ['109, 255, 179', '#6dffb3'],
  escalation: ['255, 179, 71', '#ffb347'],
  ended: ['93, 184, 214', '#5db8d6'],
};

const MOOD: Record<Phase, ReactorMood> = {
  ringing: 'ringing',
  dialing: 'ringing',
  handling: 'speaking',
  hold: 'hold',
  owner: 'owner',
  escalation: 'alert',
  ended: 'ended',
};

const HEADLINE: Record<Phase, string> = {
  ringing: 'INCOMING CALL',
  dialing: 'DIALING OUT',
  handling: 'JARVIS ON THE LINE',
  hold: 'CALLER ON HOLD',
  owner: 'OWNER ON THE LINE',
  escalation: 'AWAITING YOUR CALL',
  ended: 'CALL CLOSED',
};

const TRUST: Record<Trust, { word: string; clear: number; glyph: string }> = {
  family: { word: 'FAMILY', clear: 4, glyph: '◆' },
  work: { word: 'WORK', clear: 3, glyph: '◈' },
  unknown: { word: 'UNKNOWN', clear: 1, glyph: '◇' },
  blocked: { word: 'BLOCKED', clear: 0, glyph: '✕' },
};

const SPEAKER: Record<Speaker, [tag: string, glyph: string]> = {
  caller: ['CALLER', '◀'],
  jarvis: ['JARVIS', '▶'],
  owner: ['OWNER', '◆'],
};

const RULE_WORD: Record<CallRule['kind'], string> = {
  answer_all: 'ANSWER ALL',
  answer_unknown: 'SCREEN UNKNOWN',
  reject: 'REJECT',
  message_only: 'MESSAGE ONLY',
};

/* --------------------------------------------------------------- helpers */

const pad2 = (n: number) => String(Math.max(0, Math.floor(n))).padStart(2, '0');
export function clock(ms: number) {
  const t = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(t / 3600);
  return h ? `${h}:${pad2((t / 60) % 60)}:${pad2(t % 60)}` : `${pad2(t / 60)}:${pad2(t % 60)}`;
}

/** `+447700900123` → `+44 7700 900 123` (display only). */
export function prettyNumber(n: string) {
  const m = /^\+(\d{1,3})(\d{3,4})(\d{3})(\d{3,4})$/.exec(n.replace(/\s/g, ''));
  return m ? `+${m[1]} ${m[2]} ${m[3]} ${m[4]}` : n;
}

/** Notes and memory facts arrive as one string; split into snippets. */
function snippets(notes: string) {
  return notes
    .split(/\n|\s·\s|;\s/)
    .map((x) => x.trim())
    .filter(Boolean)
    .slice(0, 6);
}

/** 7×7 mirrored glyph sigil from the caller's number: a stable visual ID
 *  for voices you cannot see. */
function sigil(seed: string) {
  let k = 7;
  for (const c of seed) k = (k * 31 + c.charCodeAt(0)) % 9973;
  const rows: string[] = [];
  for (let y = 0; y < 7; y++) {
    let half = '';
    for (let x = 0; x < 4; x++) {
      const v = hash(k + x * 3.1, y * 7.7 + k * 0.01);
      half += v > 0.72 ? '█' : v > 0.5 ? '▓' : v > 0.34 ? '░' : '·';
    }
    rows.push(half + [...half.slice(0, 3)].reverse().join(''));
  }
  return rows;
}

function useNow(fixed: number | undefined, ms = 1000) {
  const [now, setNow] = useState(() => fixed ?? Date.now());
  useEffect(() => {
    if (fixed !== undefined) {
      setNow(fixed);
      return;
    }
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [fixed, ms]);
  return now;
}

/** Is Jarvis audibly speaking? Sampled at 8 Hz from the level ref. */
function useSpeaking(level: MutableRefObject<number>, on: boolean) {
  const [speaking, setSpeaking] = useState(false);
  useEffect(() => {
    if (!on) {
      setSpeaking(false);
      return;
    }
    const t = setInterval(() => setSpeaking(level.current > 0.08), 125);
    return () => clearInterval(t);
  }, [level, on]);
  return speaking;
}

/** Frame cycler for small CSS-free glyph animations. */
function useTick(ms: number) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((x) => x + 1), ms);
    return () => clearInterval(t);
  }, [ms]);
  return i;
}

/* ---------------------------------------------------------------- panels */

function Panel({
  label,
  index,
  aside,
  tone,
  className,
  children,
}: {
  label: string;
  index: number;
  aside?: string;
  tone?: 'warn' | 'ok';
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`${s.panel} ${className ?? ''}`} data-tone={tone}>
      <svg className={s.cornerTL} viewBox="0 0 26 26" aria-hidden="true">
        <path d="M1 26 V14 L14 1 H26" />
      </svg>
      <svg className={s.cornerBR} viewBox="0 0 26 26" aria-hidden="true">
        <path d="M25 0 V12 L12 25 H0" />
      </svg>
      <header className={s.panelHead}>
        <b>◢</b>
        <span className={s.panelLabel}>{useDecode(label, 380)}</span>
        <i className={s.panelRule} />
        {aside ? <span className={s.panelAside}>{aside}</span> : null}
        <span className={s.panelIndex}>{pad2(index)}</span>
      </header>
      <div className={s.panelBody}>{children}</div>
    </section>
  );
}

function TrustBadge({ trust, big }: { trust: Trust; big?: boolean }) {
  const t = TRUST[trust];
  return (
    <span className={s.trust} data-trust={trust} data-big={big ? 'true' : undefined}>
      <b>{t.glyph}</b>
      {t.word}
      <i>{'■'.repeat(t.clear) + '□'.repeat(4 - t.clear)}</i>
    </span>
  );
}

const Dossier = memo(function Dossier({ call, big }: { call: CallView; big: boolean }) {
  const c = call.caller;
  const name = useDecode((c?.name || 'UNIDENTIFIED CALLER').toUpperCase(), 700);
  const number = c?.number ?? '';
  const glyphs = useMemo(() => sigil(number || call.callId), [number, call.callId]);
  const notes = snippets(c?.notes ?? '');
  return (
    <div className={s.dossier} data-big={big ? 'true' : undefined}>
      <div className={s.idRow}>
        <pre className={s.sigil} data-trust={c?.trust ?? 'unknown'}>
          {glyphs.join('\n')}
        </pre>
        <div className={s.idCopy}>
          <div className={s.name}>{name}</div>
          <div className={s.number}>{number ? prettyNumber(number) : 'NUMBER WITHHELD'}</div>
          <TrustBadge trust={c?.trust ?? 'unknown'} big={big} />
        </div>
      </div>
      <dl className={s.fields}>
        <dt>RELATION</dt>
        <dd>{c?.relationship || '—'}</dd>
        <dt>CHANNEL</dt>
        <dd>WHATSAPP VOICE · {call.direction === 'in' ? 'INBOUND' : 'OUTBOUND'}</dd>
        <dt>CALL ID</dt>
        <dd>{call.callId}</dd>
      </dl>
      <div className={s.memHead}>
        <span>MEMORY</span>
        <i />
        <span>{pad2(notes.length)} FACTS</span>
      </div>
      <ul className={s.memory}>
        {notes.length ? (
          notes.map((n, i) => (
            <li key={i}>
              <b>▸</b>
              {n}
            </li>
          ))
        ) : (
          <li className={s.faint}>
            <b>·</b>No prior record. Jarvis will introduce itself.
          </li>
        )}
      </ul>
    </div>
  );
});

function Contacts({ contacts }: { contacts: QuickContact[] }) {
  return (
    <ul className={s.contacts}>
      {contacts.slice(0, 6).map((c) => (
        <li key={c.name} data-trust={c.trust}>
          <b>{TRUST[c.trust].glyph}</b>
          <span className={s.cName}>{c.name.toUpperCase()}</span>
          <span className={s.cRel}>{c.relationship}</span>
          <span className={s.cLast}>{c.last ?? ''}</span>
        </li>
      ))}
    </ul>
  );
}

/** Streaming tail for a partial line: a caret and a few unresolved glyphs. */
function PartialTail({ seed }: { seed: number }) {
  const i = useTick(90);
  return (
    <span className={s.tail}>
      {scramble('   ', 0, seed + i).replace(/ /g, '')}
      <b>▌</b>
    </span>
  );
}

const Transcript = memo(function Transcript({
  lines,
  startedAt,
}: {
  lines: TranscriptLine[];
  startedAt: number;
}) {
  if (!lines.length) {
    return <div className={s.empty}>· · · CHANNEL OPEN · NO SPEECH YET · · ·</div>;
  }
  return (
    <ol className={s.transcript}>
      {lines.map((l) =>
        l.mark ? (
          <li key={l.id} className={s.mark}>
            <i />
            <span>{l.text}</span>
            <em>+{clock(l.ts - startedAt)}</em>
            <i />
          </li>
        ) : (
          <li key={l.id} data-speaker={l.speaker} data-partial={l.partial ? 'true' : undefined}>
            <span className={s.tTime}>+{clock(l.ts - startedAt)}</span>
            <span className={s.tWho}>
              <b>{SPEAKER[l.speaker][1]}</b>
              {SPEAKER[l.speaker][0]}
            </span>
            <span className={s.tText}>
              <CalTag line={l} />
              {l.text}
              {l.partial ? <PartialTail seed={l.id} /> : null}
            </span>
          </li>
        )
      )}
    </ol>
  );
});

/** Inline marker on a Jarvis line that carries a calendar result. */
function CalTag({ line }: { line: TranscriptLine }) {
  const st = line.speaker === 'jarvis' && !line.partial ? calendarTag(line.text) : null;
  if (!st) return null;
  return (
    <span className={s.calTag} data-cal={TONE_OF[st]}>
      ◷ {STATUS_TAG[st]}
    </span>
  );
}

/* -------------------------------------------------------------- calendar */

/** ok = good for the caller, warn = waits on the owner, bad = refused. */
const TONE_OF: Record<CalStatus, 'ok' | 'warn' | 'bad' | 'dim'> = {
  free: 'ok',
  booked: 'ok',
  found: 'ok',
  pending: 'warn',
  hold: 'warn',
  ambiguous: 'warn',
  near: 'warn',
  withheld: 'warn',
  escalated: 'warn',
  clash: 'bad',
  denied: 'bad',
  missing: 'dim',
};

const KIND_WORD: Record<CalItem['kind'], string> = {
  check: 'SLOT CHECK',
  booking: 'BOOKING',
  reminder: 'REMINDER LOOKUP',
  change: 'CHANGE REQUEST',
};

const VERDICT: Record<CalStatus, [glyph: string, text: string]> = {
  free: ['●', 'SLOT IS FREE'],
  clash: ['✕', 'REQUESTED SLOT IS TAKEN'],
  booked: ['✓', 'WRITTEN TO YOUR CALENDAR'],
  pending: ['◆', 'PROPOSED · AWAITING YOUR APPROVAL'],
  hold: ['◆', 'TENTATIVE HOLD · YOU CONFIRM'],
  denied: ['✕', 'NOT BOOKED'],
  found: ['●', 'ON YOUR CALENDAR'],
  ambiguous: ['◐', 'TWO MATCHES · JARVIS ASKS WHICH'],
  near: ['◐', 'NO EXACT MATCH · CLOSEST EVENT'],
  missing: ['○', 'NOT IN YOUR CALENDAR'],
  withheld: ['◇', 'DETAILS WITHHELD · CALLER UNVERIFIED'],
  escalated: ['▲', 'SENT TO YOU · JARVIS CANNOT CHANGE EVENTS'],
};

/** What happens next, so the owner knows whether anything waits on them. */
const AFTER: Partial<Record<CalStatus, string>> = {
  free: 'JARVIS CAN OFFER THIS SLOT',
  booked: 'ADDED BY YOUR BOOKING POLICY · CALLER TOLD',
  clash: 'NO DOUBLE-BOOKING · NEAREST FREE SLOTS OFFERED',
  pending: 'NOT IN THE CALENDAR UNTIL YOU APPROVE',
  hold: 'NOT IN THE CALENDAR UNTIL YOU APPROVE',
  denied: 'CALLER ASKED FOR ANOTHER TIME',
  found: 'CALLER TOLD THE TIME',
  ambiguous: 'WAITING FOR THE CALLER TO PICK ONE',
  near: 'CALLER TOLD IT IS NOT IN AS ASKED',
  missing: 'CALLER TOLD · QUESTION FLAGGED FOR YOU',
  withheld: 'QUESTION PASSED TO YOU',
  escalated: 'WAITING ON YOU',
};

const DAY_FROM = 8 * 60;
const DAY_TO = 21 * 60;
const CELLS = (DAY_TO - DAY_FROM) / 30;

/** 08:00-21:00 in half-hour cells: the event or booking (█), the clash
 *  (✕) and the offered alternatives (◇). */
function DayStrip({ item }: { item: CalItem }) {
  const marks = new Map<number, ['main' | 'alt' | 'bad', string]>();
  const put = (when: string | null, k: 'main' | 'alt' | 'bad', g: string) => {
    const m = minutesOf(when);
    if (m === null || m < DAY_FROM || m >= DAY_TO) return;
    const i = Math.floor((m - DAY_FROM) / 30);
    if (!marks.has(i)) marks.set(i, [k, g]);
  };
  if (item.conflict) put(item.conflict.when, 'bad', '✕');
  put(item.when, item.status === 'clash' ? 'bad' : 'main', item.status === 'clash' ? '✕' : '█');
  item.alts.forEach((a) => put(a, 'alt', '◇'));
  item.matches.forEach((a) => put(a.when, 'alt', '▒'));
  if (!marks.size) return null;
  const day = (item.when ?? item.alts[0] ?? item.matches[0]?.when ?? '').split(' · ')[0];
  return (
    <div className={s.dayStrip}>
      <div className={s.dayCells}>
        {Array.from({ length: CELLS }, (_, i) => {
          const m = marks.get(i);
          return (
            <span key={i} data-k={m?.[0]} data-hour={i % 2 === 0 ? 'true' : undefined}>
              {m ? m[1] : i % 2 === 0 ? '┊' : '·'}
            </span>
          );
        })}
      </div>
      <div className={s.dayScale}>
        <span>08</span>
        <span>12</span>
        <span>16</span>
        <span>21</span>
      </div>
      {day ? <div className={s.dayName}>{day}</div> : null}
    </div>
  );
}

function CalCard({
  item,
  startedAt,
  latest,
}: {
  item: CalItem;
  startedAt: number;
  latest: boolean;
}) {
  const tone = TONE_OF[item.status];
  const [g, verdict] = VERDICT[item.status];
  const verdictText = useDecode(verdict, 420);
  if (!latest) {
    return (
      <li className={s.calRow} data-cal={tone}>
        <b>{g}</b>
        <span>{KIND_WORD[item.kind]}</span>
        <em>{STATUS_TAG[item.status]}</em>
        <small>+{clock(item.ts - startedAt)}</small>
      </li>
    );
  }
  const subject =
    item.title || item.when ? (
      <div className={s.calSubject}>
        <strong>{(item.title ?? 'REQUESTED SLOT').toUpperCase()}</strong>
        {item.when ? <span>{item.when}</span> : null}
      </div>
    ) : null;
  return (
    <li className={s.calCard} data-cal={tone}>
      <header className={s.calHead}>
        <b>◷</b>
        <span>{KIND_WORD[item.kind]}</span>
        <i />
        <small>+{clock(item.ts - startedAt)}</small>
      </header>
      {item.ask ? <q className={s.calAsk}>{item.ask}</q> : null}
      <div className={s.calVerdict}>
        <b>{g}</b>
        {verdictText}
      </div>
      {subject}
      {item.reason ? <p className={s.calReason}>{item.reason}</p> : null}
      {item.status === 'clash' ? (
        <div className={s.calConflict}>
          <em>CONFLICT</em>
          {item.conflict ? (
            <span>
              {item.conflict.title.toUpperCase()} · {item.conflict.when}
            </span>
          ) : (
            <span>EXISTING EVENT · NO DOUBLE-BOOKING</span>
          )}
        </div>
      ) : null}
      {item.alts.length ? (
        <div className={s.calAlts}>
          <em>OFFERED INSTEAD</em>
          <ol>
            {item.alts.map((a, k) => (
              <li key={a} data-lead={k === 0 ? 'true' : undefined}>
                <b>◇</b>
                {a}
                {k === 0 ? <small>NEAREST</small> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : item.status === 'clash' && item.kind === 'booking' ? (
        <div className={s.calAlts}>
          <em>OFFERED INSTEAD</em>
          <p>NO FREE SLOT NEARBY</p>
        </div>
      ) : null}
      {item.matches.length ? (
        <div className={s.calAlts}>
          <em>{item.status === 'ambiguous' ? 'ALSO MATCHES' : 'CLOSEST'}</em>
          <ol>
            {item.matches.map((m) => (
              <li key={m.title + m.when}>
                <b>▒</b>
                {m.title.toUpperCase()} · {m.when}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      <DayStrip item={item} />
      {AFTER[item.status] ? <div className={s.calAfter}>▸ {AFTER[item.status]}</div> : null}
    </li>
  );
}

const CalendarFeed = memo(function CalendarFeed({
  items,
  startedAt,
}: {
  items: CalItem[];
  startedAt: number;
}) {
  const last = items[items.length - 1];
  return (
    <>
      <ol className={s.calendar}>
        {items.slice(-4, -1).map((it) => (
          <CalCard key={it.id} item={it} startedAt={startedAt} latest={false} />
        ))}
        <CalCard key={last.id} item={last} startedAt={startedAt} latest />
      </ol>
      <footer className={s.calSource}>
        <span>SOURCE</span>
        <i />
        <span>{last.source === 'stream' ? 'CALENDAR TOOL EVENT' : 'SPOKEN TOOL RESULT'}</span>
      </footer>
    </>
  );
});

/** Who held the floor: share of transcript characters per speaker. */
function Floor({ lines }: { lines: TranscriptLine[] }) {
  const tot: Record<Speaker, number> = { caller: 0, jarvis: 0, owner: 0 };
  for (const l of lines) if (!l.mark) tot[l.speaker] += l.text.length;
  const all = tot.caller + tot.jarvis + tot.owner || 1;
  return (
    <div className={s.floor}>
      {(['caller', 'jarvis', 'owner'] as Speaker[]).map((k) => {
        const pct = (tot[k] / all) * 100;
        const [on, off] = barParts(pct, 14, { fine: true });
        return (
          <span key={k} data-speaker={k}>
            <em>{SPEAKER[k][0]}</em>
            <b>{on}</b>
            <i>{off}</i>
            <small>{String(Math.round(pct)).padStart(3, ' ')}%</small>
          </span>
        );
      })}
      <span className={s.turns}>
        <em>TURNS</em>
        <strong>{pad2(lines.filter((l) => !l.partial && !l.mark).length)}</strong>
      </span>
    </div>
  );
}

const RING = ['·    ', '◦·   ', '○◦·  ', '◎○◦· ', '◉◎○◦·', ' ◉◎○◦', '  ◉◎○', '   ◉◎', '    ◉'];

function Intercept({
  call,
  phase,
  now,
  disposition,
}: {
  call: CallView;
  phase: Phase;
  now: number;
  disposition?: string[];
}) {
  const i = useTick(140);
  const frame = RING[i % RING.length];
  const rings = Math.floor((now - call.stateSince) / 3000) + 1;
  const trust = call.caller?.trust ?? 'unknown';
  const lines =
    disposition ??
    (phase === 'dialing'
      ? ['ROUTE ▸ WHATSAPP · VOICE', 'JARVIS OPENS · AI DISCLOSED']
      : trust === 'family'
        ? ['PROTOCOL ▸ PATCH THROUGH', 'JARVIS ANSWERS IF YOU STAY SILENT']
        : trust === 'blocked'
          ? ['PROTOCOL ▸ REJECT', 'NUMBER ON DENY LIST']
          : ['PROTOCOL ▸ JARVIS SCREENS', 'AI DISCLOSED AT PICKUP']);
  return (
    <div className={s.intercept}>
      <div className={s.ringRow}>
        <pre className={s.ringGlyph}>{`${[...frame].reverse().join('')} ☏ ${frame}`}</pre>
        <div className={s.ringCount}>
          <em>{phase === 'dialing' ? 'TONE' : 'RING'}</em>
          <strong>{pad2(rings)}</strong>
        </div>
      </div>
      <ul className={s.disposition}>
        {lines.map((l, k) => (
          <li key={k} data-first={k === 0 ? 'true' : undefined}>
            {l}
          </li>
        ))}
      </ul>
      <div className={s.moveHead}>
        <span>YOUR MOVE</span>
        <i />
        <span>SPEAK ANY TIME</span>
      </div>
      <ul className={s.moves}>
        {HINTS[phase].map((k, n) => (
          <li key={k} data-lead={n === 0 ? 'true' : undefined}>
            <b>{n === 0 ? '◉' : '○'}</b>
            <q>{VOICE[k][0]}</q>
            <small>{VOICE[k][1]}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** ISO timestamps in escalation reasons read as `SAT 14 NOV · 19:00`. */
const ISO_RE = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g;

function Escalation({ reason, since }: { reason: string; since: number }) {
  const i = useTick(120);
  const stripe = useMemo(() => '╱'.repeat(220), []);
  return (
    <div className={s.escalation} role="alert">
      <div className={s.hazard} style={{ '--shift': `${-(i % 8)}ch` } as CSSProperties}>
        {stripe}
      </div>
      <div className={s.escHead}>
        <b data-on={i % 6 < 3 ? 'true' : undefined}>▲</b>
        <span>APPROVAL REQUIRED</span>
        <i />
        <small>CALLER HELD BY JARVIS · +{clock(since)}</small>
      </div>
      <p className={s.escReason}>{reason.replace(ISO_RE, fmtIso)}</p>
      <div className={s.escNote}>
        Jarvis will not share data, pay or commit you. It is stalling politely until you decide.
      </div>
    </div>
  );
}

function Debrief({ call, now }: { call: CallView; now: number }) {
  const sum = call.summary;
  const end = call.endedAt ?? now;
  const final = call.transcript.filter((l) => !l.partial && !l.mark);
  const outcome = (sum?.outcome || 'closed').replace(/_/g, ' ').toUpperCase();
  const text = useDecode(sum?.summary ?? 'Summary pending…', 900, 150);
  return (
    <div className={s.debrief}>
      <header className={s.debriefHead}>
        <b>◢</b>
        <span>DEBRIEF</span>
        <i />
        <span className={s.outcome}>■ {outcome}</span>
      </header>
      <div className={s.debriefStats}>
        <span>
          <em>DURATION</em>
          <strong>{clock(end - call.startedAt)}</strong>
        </span>
        <span>
          <em>TURNS</em>
          <strong>{pad2(final.length)}</strong>
        </span>
        <span>
          <em>LINE</em>
          <strong>{call.direction === 'in' ? 'INBOUND' : 'OUTBOUND'}</strong>
        </span>
        <span>
          <em>AI DISCLOSED</em>
          <strong>{call.aiDisclosed ? 'YES' : 'NO'}</strong>
        </span>
      </div>
      <p className={s.debriefText}>{text}</p>
      <footer className={s.debriefFoot}>
        <span>▸ MEMORY · THREADS</span>
        <span>▸ LOG {call.callId}</span>
        <span>▸ AUDIT ✓</span>
      </footer>
    </div>
  );
}

function RulesStrip({ rules, next }: { rules: CallRule[]; next?: ScheduledCall | null }) {
  return (
    <div className={s.rules}>
      <span className={s.stripLabel}>◢ AUTO-ANSWER</span>
      {rules.length ? (
        rules.map((r) => (
          <span key={r.id} className={s.rule} data-active={r.active ? 'true' : undefined}>
            <b>{r.active ? '◉' : '○'}</b>
            {RULE_WORD[r.kind]}
            <em>{r.window}</em>
            {r.note ? <small>{r.note}</small> : null}
          </span>
        ))
      ) : (
        <span className={s.rule}>
          <b>○</b>NO RULES · EVERY CALL RINGS YOU
        </span>
      )}
      <i className={s.stripFill} />
      {next ? (
        <span className={s.next}>
          <span className={s.stripLabel}>◢ NEXT CALL</span>
          <strong>{next.at}</strong>
          <span>{next.who.toUpperCase()}</span>
          <em>{next.goal}</em>
        </span>
      ) : null}
    </div>
  );
}

function VoiceHints({ phase }: { phase: Phase }) {
  const i = useTick(110);
  const mic = useMemo(
    () => Array.from({ length: 6 }, (_, k) => '▁▂▃▄▅▆▇'[Math.floor(hash(k, i) * 7)]).join(''),
    [i]
  );
  // Ringing lists its moves large in the INTERCEPT panel; the bar only points there.
  const keys = phase === 'ringing' || phase === 'dialing' ? [] : HINTS[phase];
  return (
    <div className={s.hints}>
      <span className={s.mic}>
        <b>{mic}</b>
        SAY
      </span>
      {keys.map((k) => (
        <span key={k} className={s.hint}>
          <q>{VOICE[k][0]}</q>
          <small>{VOICE[k][1]}</small>
        </span>
      ))}
      {!keys.length ? (
        <span className={s.hint}>
          <small>
            {phase === 'ended'
              ? 'line closed · overlay clears on its own'
              : 'caller waiting · speak your move ▸ intercept panel'}
          </small>
        </span>
      ) : null}
      <i className={s.stripFill} />
      <span className={s.hint} data-global="true">
        <q>{VOICE.stop_all[0]}</q>
        <small>{VOICE.stop_all[1]}</small>
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------- hud */

export type CallHudProps = {
  call: CallView;
  /** Jarvis output level 0..1 (CallFeed.level). */
  level: MutableRefObject<number>;
  /** Freeze the clock (demo / screenshots); live use omits it. */
  now?: number;
  rules?: CallRule[];
  nextCall?: ScheduledCall | null;
  contacts?: QuickContact[];
  /** Optional ringing disposition lines (what Jarvis will do). */
  disposition?: string[];
  /** `overlay` sits over the HUD (fixed, translucent); `page` is opaque. */
  variant?: 'overlay' | 'page';
};

/** Jarvis Calls HUD: one full-screen glyph surface per live call. Voice
 *  only: no pointer targets, every action is a spoken hint. */
export function CallHud({
  call,
  level,
  now: fixedNow,
  rules = [],
  nextCall,
  contacts = [],
  disposition,
  variant = 'overlay',
}: CallHudProps) {
  const now = useNow(fixedNow);
  const phase = phaseOf(call);
  const [rgb, hex] = TONE[phase];
  const speaking = useSpeaking(level, phase === 'handling' || phase === 'escalation');
  const ringingish = phase === 'ringing' || phase === 'dialing';
  const elapsed = (call.endedAt ?? now) - call.startedAt;
  const inState = now - call.stateSince;
  const headline = useDecode(HEADLINE[phase], 520);
  const lastSpeaker = call.transcript.findLast((l) => !l.mark)?.speaker;
  const calItems = useMemo(() => calendarItems(call), [call]);
  const showCal = calItems.length > 0 && !ringingish;
  const calTone = showCal ? TONE_OF[calItems[calItems.length - 1].status] : null;

  const sub =
    phase === 'handling'
      ? speaking
        ? 'SPEAKING'
        : lastSpeaker === 'caller'
          ? 'LISTENING TO CALLER'
          : 'LISTENING'
      : phase === 'hold'
        ? `HELD ${clock(inState)} · HOLD TONE PLAYING`
        : phase === 'owner'
          ? 'JARVIS MONITORING · SILENT'
          : phase === 'escalation'
            ? 'JARVIS HOLDING THE LINE'
            : phase === 'ended'
              ? `DURATION ${clock(elapsed)}`
              : phase === 'dialing'
                ? `${(call.caller?.name ?? 'CONTACT').toUpperCase()} · CONNECTING`
                : `${(call.caller?.name ?? 'UNKNOWN').toUpperCase()} · ${clock(inState)}`;

  return (
    <div
      className={s.root}
      data-variant={variant}
      data-phase={phase}
      style={{ '--acc': rgb } as CSSProperties}
      aria-live="polite"
    >
      <div className={s.backdrop} aria-hidden="true" />
      <i className={s.edge} aria-hidden="true" />

      <header className={s.top}>
        <div className={s.brand}>
          <pre className={s.emblem}>{'╭─╮\n│◉│\n╰─╯'}</pre>
          <div>
            <div className={s.wordmark}>J.A.R.V.I.S. // COMMS</div>
            <div className={s.brandSub}>LOCKE OS · CALL INTERCEPT · WHATSAPP</div>
          </div>
        </div>
        <div className={s.chips}>
          <span className={s.chip} data-on="true">
            <b>●</b>
            {phase === 'escalation' ? 'ESCALATED' : phase.toUpperCase()}
          </span>
          <span
            className={s.chip}
            data-state={call.aiDisclosed ? 'ok' : ringingish ? undefined : 'warn'}
          >
            <b>{call.aiDisclosed ? '◇' : ringingish ? '◌' : '▲'}</b>
            {call.aiDisclosed
              ? 'AI DISCLOSED'
              : ringingish
                ? 'AI DISCLOSURE ARMED'
                : 'AI NOT DISCLOSED'}
          </span>
          <span className={s.chip} data-state={call.recording ? 'rec' : undefined}>
            <b>{call.recording ? '◉' : '○'}</b>
            {call.recording ? 'REC · NOTICE GIVEN' : 'NOT RECORDING'}
          </span>
          <span className={s.chip}>
            <b>◈</b>
            VOICE · {call.mode === 'owner_voice' ? 'OWNER PERSONA' : 'JARVIS'}
          </span>
        </div>
        <div className={s.timer}>
          <div className={s.timerBig}>{ringingish ? clock(inState) : clock(elapsed)}</div>
          <div className={s.timerSub}>
            {ringingish
              ? phase === 'dialing'
                ? 'DIALING'
                : 'RINGING'
              : phase === 'ended'
                ? 'CALL ENDED'
                : 'ON CALL'}{' '}
            · {call.direction === 'in' ? 'INBOUND' : 'OUTBOUND'}
          </div>
        </div>
      </header>

      <main className={s.grid}>
        <aside className={s.left}>
          <Panel
            label="CALLER DOSSIER"
            index={1}
            aside={call.caller ? TRUST[call.caller.trust].word : 'NO MATCH'}
            className={ringingish ? s.grow : undefined}
          >
            <Dossier call={call} big={ringingish} />
          </Panel>
          {showCal ? (
            <Panel
              label="CALENDAR"
              index={2}
              aside={`${pad2(calItems.length)} ${calItems.length === 1 ? 'LOOKUP' : 'LOOKUPS'}`}
              className={s.grow}
              tone={calTone === 'warn' || calTone === 'ok' ? calTone : undefined}
            >
              <CalendarFeed items={calItems} startedAt={call.startedAt} />
            </Panel>
          ) : null}
          {contacts.length && !showCal ? (
            <Panel
              label="CONTACTS"
              index={2}
              aside={`${contacts.length} KNOWN`}
              className={ringingish ? undefined : s.grow}
            >
              <Contacts contacts={contacts} />
            </Panel>
          ) : null}
        </aside>

        <section className={s.center}>
          <div className={s.goal}>
            <em>OBJECTIVE</em>
            <span>{call.goal || 'Screen the caller, take a message, protect the owner.'}</span>
          </div>
          {phase === 'escalation' && call.escalation ? (
            <Escalation reason={call.escalation.reason} since={now - call.escalation.at} />
          ) : null}
          <div className={s.reactorWrap}>
            <CallReactor mood={MOOD[phase]} color={hex} level={level} className={s.reactor} />
            <svg className={s.dial} viewBox="0 0 100 100" aria-hidden="true">
              <circle cx="50" cy="50" r="48.5" />
              {Array.from({ length: 72 }, (_, k) => {
                const a = (k / 72) * Math.PI * 2;
                const r0 = k % 6 === 0 ? 45.5 : 47;
                return (
                  <line
                    key={k}
                    x1={50 + Math.cos(a) * r0}
                    y1={50 + Math.sin(a) * r0}
                    x2={50 + Math.cos(a) * 48.5}
                    y2={50 + Math.sin(a) * 48.5}
                  />
                );
              })}
            </svg>
            {phase === 'ended' ? <Debrief call={call} now={now} /> : null}
          </div>
          <div className={s.readout}>
            <div className={s.headline}>{headline}</div>
            <div className={s.subline}>{sub}</div>
          </div>
          <CallWave mood={MOOD[phase]} color={hex} level={level} className={s.wave} />
          {!ringingish ? <Floor lines={call.transcript} /> : null}
        </section>

        <aside className={s.right}>
          {ringingish ? (
            <Panel
              label={phase === 'dialing' ? 'OUTBOUND LINK' : 'INTERCEPT'}
              index={3}
              aside={phase === 'dialing' ? 'DIALING' : 'LIVE'}
              className={s.grow}
            >
              <Intercept call={call} phase={phase} now={now} disposition={disposition} />
            </Panel>
          ) : (
            <Panel
              label="LIVE TRANSCRIPT"
              index={3}
              aside={call.recording ? 'REC' : 'EPHEMERAL'}
              className={s.grow}
              tone={phase === 'escalation' ? 'warn' : phase === 'owner' ? 'ok' : undefined}
            >
              <Transcript lines={call.transcript} startedAt={call.startedAt} />
            </Panel>
          )}
        </aside>
      </main>

      <footer className={s.foot}>
        <RulesStrip rules={rules} next={nextCall} />
        <VoiceHints phase={phase} />
      </footer>
    </div>
  );
}
