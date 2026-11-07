'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import {
  type CalendarRecord,
  type CalendarStatus,
  calendarStatus,
  dismissCalendarSuggestion,
} from '@/lib/auto-calendar';
import { startVisiblePoll } from '@/lib/visible-poll';
import s from './calendar-dashboard.module.css';

const STATUS_LABELS: Record<string, string> = {
  added: 'Added to calendar',
  retry: 'Waiting to retry',
  'extract-retry': 'Waiting to retry',
  pending: 'Needs review',
  'review-required': 'Needs review',
  dismissed: 'Dismissed',
  undone: 'Undone',
  duplicate: 'Already on calendar',
};

function EventRow({
  item,
  dismiss,
  busy,
}: {
  item: CalendarRecord;
  dismiss?: (id: string) => void;
  busy?: boolean;
}) {
  const title = item.title || item.event?.title || 'Untitled event';
  const when =
    item.when ||
    [item.date || item.event?.date, item.start || item.event?.start].filter(Boolean).join(' · ');
  return (
    <li className={s.event}>
      <div className={s.eventMark} aria-hidden="true">
        {item.status === 'added' ? '✓' : '◷'}
      </div>
      <div className={s.eventBody}>
        <div className={s.eventTop}>
          <h3>{title}</h3>
          <span className={s.badge} data-status={item.status}>
            {STATUS_LABELS[item.status || 'pending'] || item.status}
          </span>
        </div>
        {when && <p className={s.when}>{when}</p>}
        <p className={s.meta}>
          {[
            item.source === 'whatsapp'
              ? 'WhatsApp'
              : ['email', 'mail'].includes(item.source || '')
                ? 'Email'
                : item.source || 'Message',
            item.sender,
            item.location || item.event?.location,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
        {(item.reason || item.error) && <p className={s.reason}>{item.reason || item.error}</p>}
        {item.google_id && <p className={s.audit}>Calendar event · {item.google_id}</p>}
      </div>
      {dismiss && item.id && (
        <button
          className={s.dismiss}
          disabled={busy}
          onClick={() => dismiss(item.id!)}
          aria-label={`Dismiss ${title}`}
        >
          {busy ? 'Dismissing…' : 'Dismiss'}
        </button>
      )}
    </li>
  );
}

export function CalendarDashboard({ embedded = false }: { embedded?: boolean }) {
  const [data, setData] = useState<CalendarStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [offline, setOffline] = useState(false);
  const [accessError, setAccessError] = useState('');
  const [updated, setUpdated] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const stop = startVisiblePoll(async (signal) => {
      const result = await calendarStatus(signal);
      if (signal.aborted) return;
      setLoaded(true);
      const refusal =
        result?.ok === false ? result.error || 'Calendar activity is unavailable.' : '';
      setAccessError(refusal);
      setOffline(!result?.ok && !refusal);
      if (refusal) {
        setData(null);
        setNotice('');
      }
      if (result?.ok) {
        setData(result);
        setUpdated(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      }
    }, 5000);
    return () => {
      alive.current = false;
      stop();
    };
  }, []);
  const dismiss = async (id: string) => {
    if (busy || offline) return;
    setBusy(id);
    setNotice('');
    const result = await dismissCalendarSuggestion(id);
    if (!alive.current) return;
    if (result?.ok) {
      setNotice('Suggestion dismissed.');
      const next = await calendarStatus();
      if (!alive.current) return;
      if (next?.ok) {
        setData(next);
        setAccessError('');
      } else if (next?.error) {
        setData(null);
        setAccessError(next.error);
        setOffline(false);
      } else setOffline(true);
    } else {
      if (/owner|unauthorized/i.test(result?.error || '')) {
        setData(null);
        setAccessError(result?.error || 'Owner access required.');
        setOffline(false);
      }
      setNotice(
        result?.error || 'Could not dismiss this suggestion. Try again when Jarvis is connected.'
      );
    }
    setBusy(null);
  };
  const pending = data?.pending || [];
  const retry = data?.retry || [];
  const recent = data?.recent || [];
  const deferred = data?.reminders?.deferred || 0;
  return (
    <main className={`${s.root} ${embedded ? s.embedded : ''}`}>
      <div className={s.content}>
        {!embedded && (
          <header className={s.header}>
            <Link href="/" className={s.brand}>
              J<span> / </span>JARVIS
            </Link>
            <span className={s.live} data-offline={offline || !loaded || !!accessError}>
              <i />
              {!loaded
                ? 'Connecting to Jarvis'
                : accessError
                  ? 'Owner access required'
                  : offline
                    ? 'Bridge unavailable'
                    : `Updated ${updated}`}
            </span>
          </header>
        )}
        <section className={s.hero}>
          <p className={s.eyebrow}>PERSONAL OPERATIONS / CALENDAR</p>
          <h1>
            Your plans,
            <br />
            <span>taken care of.</span>
          </h1>
          <p className={s.intro}>
            Events from your conversations, with a clear record of what Jarvis added and what still
            needs your attention.
          </p>
        </section>
        {accessError && (
          <div role="status" className={s.alert}>
            {accessError} Return to an owner session to view calendar activity.
          </div>
        )}
        {offline && (
          <div role="status" className={s.alert}>
            Jarvis is unreachable.{' '}
            {data
              ? 'Showing the last received activity; actions are paused.'
              : 'Start the local bridge to view your calendar activity.'}{' '}
            This page reconnects automatically.
          </div>
        )}
        <div className={s.stats}>
          <section className={s.stat}>
            <span>EVENT HANDLING</span>
            <strong>{!data ? '—' : data.enabled ? 'Automatic' : 'Suggestions'}</strong>
            <p>
              {data?.enabled
                ? 'Eligible events are added automatically.'
                : data
                  ? 'Events wait for your confirmation.'
                  : 'Waiting for feature status.'}
            </p>
          </section>
          <section className={s.stat}>
            <span>NEEDS ATTENTION</span>
            <strong>{data ? String(pending.length + retry.length).padStart(2, '0') : '—'}</strong>
            <p>Suggestions and events waiting to retry.</p>
          </section>
          <section className={s.stat}>
            <span>RETURN REMINDERS</span>
            <strong>{data ? String(deferred).padStart(2, '0') : '—'}</strong>
            <p>{data?.reminders?.detail || 'Waiting for reminder status.'}</p>
          </section>
        </div>
        <div className={s.columns}>
          <div className={s.activity}>
            <section className={s.panel} aria-labelledby="attention-title">
              <div className={s.panelHead}>
                <h2 id="attention-title">Needs attention</h2>
                <span>{pending.length}</span>
              </div>
              <p className={s.description}>
                Review event suggestions by voice. Dismissing a suggestion leaves your calendar
                unchanged.
              </p>
              <p className={s.notice} role="status" aria-live="polite">
                {notice}
              </p>
              {pending.length ? (
                <ul className={s.events}>
                  {pending.map((item, index) => (
                    <EventRow
                      key={item.id || index}
                      item={item}
                      dismiss={offline ? undefined : dismiss}
                      busy={!!busy}
                    />
                  ))}
                </ul>
              ) : (
                <div className={s.empty}>
                  <span aria-hidden="true">✓</span>
                  <h3>{!data ? 'Waiting for Jarvis' : 'All clear'}</h3>
                  <p>
                    {!data
                      ? 'Your event activity will appear here once connected.'
                      : 'No suggestions are waiting for your attention.'}
                  </p>
                </div>
              )}
            </section>
            {retry.length > 0 && (
              <section className={s.panel} aria-labelledby="retry-title">
                <div className={s.panelHead}>
                  <h2 id="retry-title">Waiting to retry</h2>
                  <span>{retry.length}</span>
                </div>
                <p className={s.description}>
                  Event extraction or calendar writes that could not complete. Jarvis retries these
                  automatically.
                </p>
                <ul className={s.events}>
                  {retry.map((item, index) => (
                    <EventRow key={item.id || index} item={item} />
                  ))}
                </ul>
              </section>
            )}
            <section className={s.panel} aria-labelledby="activity-title">
              <div className={s.panelHead}>
                <h2 id="activity-title">Recent calendar activity</h2>
                <span>EVENT LOG</span>
              </div>
              <p className={s.description}>
                A record of automatic additions, retries and undo actions. Dates use{' '}
                {data?.timezone || 'the configured calendar timezone'}.
              </p>
              {recent.length ? (
                <ul className={s.events}>
                  {recent.map((item, index) => (
                    <EventRow key={item.google_id || item.id || index} item={item} />
                  ))}
                </ul>
              ) : (
                <div className={s.empty}>
                  <h3>No activity yet</h3>
                  <p>When Jarvis processes an event, its result will appear here.</p>
                </div>
              )}
            </section>
          </div>
          <aside className={s.sidebar}>
            <section className={s.panel}>
              <div className={s.panelHead}>
                <h2>Sources & destination</h2>
              </div>
              <div className={s.source}>
                <b>WhatsApp</b>
                <span className={s.badge}>
                  {!data
                    ? 'Waiting'
                    : data.whatsapp?.connected
                      ? 'Session observed'
                      : data.whatsapp?.enabled
                        ? 'Enabled · unverified'
                        : 'Disabled'}
                </span>
                <p>{data?.whatsapp?.detail || 'Connection status has not been verified.'}</p>
                {data?.whatsapp?.last_scan && (
                  <p className={s.identity}>
                    Last scan · {new Date(data.whatsapp.last_scan * 1000).toLocaleString()}
                  </p>
                )}
              </div>
              <div className={s.source}>
                <b>Google Calendar</b>
                <span className={s.badge}>
                  {!data
                    ? 'Waiting'
                    : data.calendar?.connected
                      ? 'Verified'
                      : data.calendar?.configured
                        ? 'Configured · unverified'
                        : 'Setup required'}
                </span>
                <p>{data?.calendar?.detail || 'Calendar connection has not been verified.'}</p>
                {data?.calendar?.verified_at && (
                  <p className={s.identity}>
                    Last verified · {new Date(data.calendar.verified_at * 1000).toLocaleString()}
                  </p>
                )}
                {data?.calendar?.account && (
                  <p className={s.identity}>Account · {data.calendar.account}</p>
                )}
                {data?.calendar?.calendar_id && (
                  <p className={s.identity}>Calendar · {data.calendar.calendar_id}</p>
                )}
              </div>
            </section>
            <section className={`${s.panel} ${s.returnPanel}`}>
              <p className={s.eyebrow}>WHEN YOU RETURN</p>
              <h2>A timely welcome back.</h2>
              <p>
                {data?.presence?.detail ||
                  'Jarvis uses your configured presence signals to decide when to deliver deferred reminders.'}
              </p>
              <div className={s.presence}>
                <i />
                {data?.presence?.state === 'input'
                  ? 'Keyboard / mouse activity'
                  : data?.presence?.state || 'Presence unavailable'}
              </div>
            </section>
            <section className={s.hint}>
              <span>TRY SAYING</span>
              <p>“Show my calendar”</p>
              <p>“Undo the last auto event”</p>
              <small>Undo applies to the last event Jarvis added automatically.</small>
            </section>
          </aside>
        </div>
        <footer className={s.footer}>
          JARVIS / CALENDAR ACTIVITY
          <span>Refreshes automatically · {data?.timezone || 'Timezone pending'}</span>
        </footer>
      </div>
    </main>
  );
}
