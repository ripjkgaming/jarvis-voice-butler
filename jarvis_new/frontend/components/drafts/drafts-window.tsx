'use client';

/** Drafts window: reply drafts Jarvis wrote for mail Sir has not answered.
 *  Same workshop-monitor look as the Project Archive.
 *
 *  VOICE-ONLY by design: the cursor never touches this window and nothing
 *  here is clickable. Sir asks for it ("open my drafts"), reads and sends by
 *  voice, and the screen only reflects the drafts on file. When the last
 *  draft is sent or discarded the window asks the bridge to hide it. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSurfaceVisibility } from '@/hooks/hud/use-surface-visibility';
import { type ReplyDraft, closeIfEmpty, listDrafts } from '@/lib/drafts';
import styles from './drafts-window.module.css';

const POLL_MS = 2000;

const PHRASES = [
  'read it',
  'send it',
  'change it to say …',
  'discard it',
  'next draft',
  'close drafts',
];

const pad = (n: number) => String(n).padStart(2, '0');

function senderName(d: ReplyDraft): string {
  const raw = (d.sender || d.to || 'Unknown').trim();
  const m = raw.match(/^"?([^"<]+?)"?\s*</);
  return (m ? m[1] : raw).trim();
}

function age(created: ReplyDraft['created'], now: number): string {
  const t = typeof created === 'string' ? Date.parse(created) / 1000 : created;
  if (!t || Number.isNaN(t)) return '';
  const s = Math.max(0, Math.floor(now / 1000 - t));
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function DraftsWindow() {
  useSurfaceVisibility();
  const [drafts, setDrafts] = useState<ReplyDraft[] | null>(null);
  // Static export and the first browser render must share a stable clock.
  const [now, setNow] = useState(0);
  const had = useRef(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const list = await listDrafts();
      if (!alive) return;
      setNow(Date.now());
      setDrafts(list);
      if (list === null) return;
      if (list.length > 0) {
        had.current = true;
      } else if (had.current) {
        // The last draft is gone: ask the bridge to hide this window.
        had.current = false;
        void closeIfEmpty();
      }
    };
    void tick();
    const id = setInterval(() => void tick(), POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const offline = drafts === null;
  const list = useMemo(() => drafts ?? [], [drafts]);
  const current = list[0];
  const high = list.filter((d) => d.priority === 'high').length;

  return (
    <div className={styles.root}>
      <div className={styles.grid} aria-hidden />

      <header className={styles.header}>
        <div className={styles.brand}>
          <svg viewBox="0 0 120 120" className={styles.mark} aria-hidden>
            <path d="M60 8 L112 104 H8 Z" />
            <path d="M60 40 L60 72 M60 84 L60 88" className={styles.markBang} />
          </svg>
          <div>
            <strong>DRAFTS</strong>
            <small>REPLIES AWAITING APPROVAL</small>
          </div>
        </div>
        <div className={styles.stats}>
          <span>
            <b>{list.length}</b>ON FILE
          </span>
          <span>
            <b className={high ? styles.hot : undefined}>{high}</b>URGENT
          </span>
        </div>
        <div className={styles.clock}>
          {offline ? <em className={styles.offline}>BRIDGE OFFLINE</em> : null}
          {now
            ? new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : '--:--'}
        </div>
      </header>

      <main className={styles.monitors}>
        <section className={styles.monitor} aria-label="Draft index">
          <div className={styles.monTag}>MON-A // INBOX</div>
          <ol className={styles.list}>
            {drafts === null && <li className={styles.hint}>Reaching the bridge…</li>}
            {drafts !== null && list.length === 0 && (
              <li className={styles.hint}>No drafts, Sir.</li>
            )}
            {list.map((d, i) => (
              <li key={d.id} className={i === 0 ? styles.rowOn : styles.row}>
                <span className={styles.rowNum}>{pad(i + 1)}</span>
                <span className={styles.rowBody}>
                  <span className={styles.rowTitle}>{senderName(d)}</span>
                  <span className={styles.rowSub}>{d.subject.replace(/^(re:\s*)+/i, '')}</span>
                  <span className={styles.rowMeta}>{age(d.created, now)}</span>
                </span>
                {d.priority === 'high' ? <span className={styles.urgent}>URGENT</span> : null}
              </li>
            ))}
          </ol>
        </section>

        <section className={styles.sheet} aria-label="Reply draft">
          <div className={styles.monTag}>MON-B // REPLY SHEET</div>
          {current ? (
            <article className={styles.paper}>
              <dl className={styles.fields}>
                <dt>TO</dt>
                <dd>{current.to}</dd>
                <dt>RE</dt>
                <dd>{current.subject}</dd>
                <dt>STATE</dt>
                <dd>
                  <span className={current.status === 'announced' ? styles.stA : styles.stP}>
                    {current.status === 'announced' ? 'ANNOUNCED' : 'WAITING'}
                  </span>
                  <span className={styles.notSent}>NOT SENT</span>
                </dd>
              </dl>
              {current.summary ? (
                <p className={styles.said}>
                  <span>THEY WROTE</span>
                  {current.summary}
                </p>
              ) : null}
              <div className={styles.body} role="region" aria-label="Draft reply text">
                <span className={styles.bodyTag}>MY REPLY</span>
                {current.body}
              </div>
            </article>
          ) : (
            <div className={styles.empty}>
              <span>ALL CLEAR</span>
              Nothing is waiting for your approval.
            </div>
          )}
        </section>
      </main>

      <footer className={styles.phrases}>
        <span className={styles.say}>SAY</span>
        {PHRASES.map((p) => (
          <em key={p}>{p}</em>
        ))}
      </footer>
    </div>
  );
}
