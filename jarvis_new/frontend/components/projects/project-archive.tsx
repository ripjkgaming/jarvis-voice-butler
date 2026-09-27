'use client';

/** Project archive: Sir's background research (Claude Sonnet) and coding
 *  (opencode) jobs, styled after the Malibu workshop monitors. Two
 *  "monitors": an index on the left, a blueprint document sheet on the
 *  right.
 *
 *  VOICE-ONLY by design: the cursor never touches this window. Nothing
 *  here is clickable; every action arrives through the bridge's UI command
 *  bus ("navigate to the solar project, open the second document, start
 *  scrolling slowly") and the screen reflects voice state back. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Markdown } from '@/components/projects/markdown';
import {
  type ProjectDetail,
  type ProjectMeta,
  type UiCommand,
  documentsOf,
  getProject,
  listProjects,
  pollUiCommands,
} from '@/lib/projects';
import styles from './project-archive.module.css';

type Filter = 'all' | 'research' | 'code';
type Speed = 'slow' | 'medium' | 'fast';
const SPEED_PX: Record<Speed, number> = { slow: 38, medium: 90, fast: 190 };
const SPEEDS: Speed[] = ['slow', 'medium', 'fast'];

const PHRASES = [
  'open research projects',
  'navigate to the … project',
  'open project two',
  'open the second document',
  'start scrolling slowly',
  'faster · slower · stop scrolling',
  'scroll up · go to the top',
  'show code projects · show everything',
  'go back · close the document',
  'research … · build me a script that …',
  'abort this project',
  'delete this project · confirm delete',
  'close research projects',
];

function ago(ts: number, now: number): string {
  const s = Math.max(0, Math.round(now / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

const STATUS_LABEL: Record<ProjectMeta['status'], string> = {
  running: 'IN PROGRESS',
  done: 'COMPLETE',
  failed: 'FAILED',
  cancelled: 'CANCELLED',
};

function Reticle({ spinning }: { spinning?: boolean }) {
  return (
    <svg
      viewBox="0 0 120 120"
      className={spinning ? styles.reticleSpin : styles.reticle}
      aria-hidden
    >
      <circle cx="60" cy="60" r="54" />
      <circle cx="60" cy="60" r="38" strokeDasharray="4 6" />
      <circle cx="60" cy="60" r="20" />
      <path d="M60 0v22M60 98v22M0 60h22M98 60h22" />
      <path d="M60 60 L98 34" className={styles.reticleArm} />
    </svg>
  );
}

type Heard = { at: number; text: string };

export function ProjectArchive() {
  const [projects, setProjects] = useState<ProjectMeta[] | null>(null);
  const [offline, setOffline] = useState(false);
  const [filter, setFilter] = useState<Filter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [docIndex, setDocIndex] = useState<number | null>(null);
  const [scrolling, setScrolling] = useState(false);
  const [speed, setSpeed] = useState<Speed>('slow');
  const [progress, setProgress] = useState(0);
  // 0 until mounted: the static export prerenders this page, so a real
  // clock at build time would mismatch on hydration.
  const [now, setNow] = useState(0);
  const [heard, setHeard] = useState<Heard[]>([]);
  // Voice delete is two-step: the bridge parks it until "confirm delete".
  const [pendingDelete, setPendingDelete] = useState<{ id: string; at: number } | null>(null);

  const sheetRef = useRef<HTMLDivElement | null>(null);
  const docs = useMemo(() => documentsOf(detail), [detail]);
  const openDoc = docIndex !== null ? (docs[docIndex] ?? null) : null;
  const visible = useMemo(
    () => (projects ?? []).filter((p) => filter === 'all' || p.kind === filter),
    [projects, filter]
  );
  const visibleRef = useRef<ProjectMeta[]>([]);
  visibleRef.current = visible;

  // --- data --------------------------------------------------------------
  const refreshList = useCallback(async () => {
    const list = await listProjects();
    if (list === null) {
      setOffline(true);
      return;
    }
    setOffline(false);
    setProjects(list);
  }, []);

  useEffect(() => {
    refreshList();
    const t = window.setInterval(refreshList, 4000);
    return () => window.clearInterval(t);
  }, [refreshList]);

  useEffect(() => {
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;

  const selectedRunning = projects?.find((p) => p.id === selectedId)?.status === 'running';
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let alive = true;
    const load = async () => {
      const d = await getProject(selectedId);
      if (alive && d) setDetail(d);
    };
    load();
    const t = window.setInterval(load, selectedRunning ? 3000 : 15000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [selectedId, selectedRunning]);

  // --- auto-scroll -------------------------------------------------------
  useEffect(() => {
    if (!scrolling || !openDoc) return;
    let raf = 0;
    let last = performance.now();
    let carry = 0;
    const step = (t: number) => {
      const el = sheetRef.current;
      if (!el) return;
      carry += (SPEED_PX[speed] * (t - last)) / 1000;
      last = t;
      const whole = Math.floor(carry);
      if (whole >= 1) {
        el.scrollTop += whole;
        carry -= whole;
      }
      if (el.scrollTop + el.clientHeight >= el.scrollHeight - 1) {
        setScrolling(false);
        return;
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [scrolling, speed, openDoc]);

  const onSheetScroll = () => {
    const el = sheetRef.current;
    if (!el) return;
    const max = el.scrollHeight - el.clientHeight;
    setProgress(max > 0 ? el.scrollTop / max : 0);
  };

  useEffect(() => {
    if (sheetRef.current) sheetRef.current.scrollTop = 0;
    setProgress(0);
  }, [docIndex, selectedId]);

  // --- voice commands ------------------------------------------------------
  const selectProject = useCallback((id: string | null) => {
    setSelectedId(id);
    setDocIndex(null);
    setScrolling(false);
  }, []);

  const openDocument = useCallback(
    (oneBased: number) => {
      const n = docs.length;
      if (!n) return false;
      const i = oneBased < 0 ? n - 1 : Math.min(Math.max(oneBased, 1), n) - 1;
      setDocIndex(i);
      setScrolling(false);
      return true;
    },
    [docs.length]
  );

  const scrollCmd = useCallback((cmd: UiCommand) => {
    const el = sheetRef.current;
    switch (cmd.mode) {
      case 'start':
        if (cmd.speed) setSpeed(cmd.speed);
        setScrolling(true);
        break;
      case 'stop':
        setScrolling(false);
        break;
      case 'faster':
        setSpeed((s) => SPEEDS[Math.min(SPEEDS.indexOf(s) + 1, SPEEDS.length - 1)]);
        setScrolling(true);
        break;
      case 'slower':
        setSpeed((s) => SPEEDS[Math.max(SPEEDS.indexOf(s) - 1, 0)]);
        break;
      case 'down':
        el?.scrollBy({ top: el.clientHeight * 0.8, behavior: 'smooth' });
        break;
      case 'up':
        el?.scrollBy({ top: -el.clientHeight * 0.8, behavior: 'smooth' });
        break;
      case 'top':
        setScrolling(false);
        el?.scrollTo({ top: 0, behavior: 'smooth' });
        break;
      case 'bottom':
        setScrolling(false);
        el?.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
        break;
    }
  }, []);

  // Commands can outrun data in a chained utterance ("navigate to X and
  // open the first document and start scrolling"): hold them until ready.
  const pendingDoc = useRef<number | null>(null);
  useEffect(() => {
    if (pendingDoc.current !== null && docs.length) {
      openDocument(pendingDoc.current);
      pendingDoc.current = null;
    }
  }, [docs.length, openDocument]);
  const pendingScroll = useRef<UiCommand | null>(null);
  useEffect(() => {
    if (pendingScroll.current && openDoc) {
      const c = pendingScroll.current;
      pendingScroll.current = null;
      window.setTimeout(() => scrollCmd(c), 250);
    }
  }, [openDoc, scrollCmd]);

  const runCommand = useCallback(
    (cmd: UiCommand) => {
      if (cmd.heard) {
        setHeard((h) => [{ at: Date.now(), text: cmd.heard as string }, ...h].slice(0, 5));
      }
      switch (cmd.action) {
        case 'show':
          break;
        case 'filter':
          if (cmd.filter) setFilter(cmd.filter);
          break;
        case 'select': {
          let id = cmd.project_id ?? null;
          if (!id && typeof cmd.index === 'number') {
            const list = visibleRef.current;
            const i = cmd.index < 0 ? list.length - 1 : cmd.index - 1;
            id = list[i]?.id ?? null;
          }
          if (id) selectProject(id);
          break;
        }
        case 'open_document': {
          const idx = cmd.index ?? 1;
          if (!openDocument(idx)) pendingDoc.current = idx;
          break;
        }
        case 'close_document':
          setDocIndex(null);
          setScrolling(false);
          break;
        case 'back':
          if (docIndex !== null) {
            setDocIndex(null);
            setScrolling(false);
          } else selectProject(null);
          break;
        case 'scroll':
          if (!sheetRef.current && cmd.mode !== 'stop') pendingScroll.current = cmd;
          else scrollCmd(cmd);
          break;
        case 'delete_pending':
          if (cmd.project_id) {
            selectProject(cmd.project_id);
            setPendingDelete({ id: cmd.project_id, at: Date.now() });
          }
          break;
        case 'deleted':
          setPendingDelete(null);
          if (cmd.project_id && selectedIdRef.current === cmd.project_id) selectProject(null);
          refreshList();
          break;
      }
    },
    [docIndex, openDocument, scrollCmd, selectProject, refreshList]
  );

  const runRef = useRef(runCommand);
  runRef.current = runCommand;
  useEffect(() => {
    let cursor: number | null = null;
    let alive = true;
    let busy = false;
    const tick = async () => {
      if (busy) return;
      busy = true;
      const r = await pollUiCommands(cursor);
      busy = false;
      if (!alive || !r) return;
      if (cursor !== null) {
        for (const c of r.commands) runRef.current(c);
      }
      cursor = r.seq;
    };
    tick();
    const t = window.setInterval(tick, 600);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  // --- derived -----------------------------------------------------------
  const running = (projects ?? []).filter((p) => p.status === 'running').length;
  const complete = (projects ?? []).filter((p) => p.status === 'done').length;
  const clock = new Date(now);
  const meta = detail ?? projects?.find((p) => p.id === selectedId) ?? null;
  const latest = heard[0] && now - heard[0].at < 4000 ? heard[0] : null;

  return (
    <div className={styles.root}>
      <div className={styles.grid} aria-hidden />

      <header className={styles.header}>
        <div className={styles.brand}>
          <Reticle spinning={running > 0} />
          <div>
            <strong>J.A.R.V.I.S.</strong>
            <small>PROJECT ARCHIVE</small>
          </div>
        </div>
        <div className={styles.stats}>
          <span>
            <b>{pad(running)}</b> ACTIVE
          </span>
          <span>
            <b>{pad(complete)}</b> COMPLETE
          </span>
          <span>
            <b>{pad(projects?.length ?? 0)}</b> ON FILE
          </span>
        </div>
        <div className={styles.clock}>
          {offline ? <em className={styles.offline}>BRIDGE OFFLINE</em> : null}
          <span>
            {now
              ? `${pad(clock.getHours())}:${pad(clock.getMinutes())}:${pad(clock.getSeconds())}`
              : '--:--:--'}
          </span>
        </div>
      </header>

      <main className={styles.monitors}>
        {/* ---------- MONITOR A: index + voice console ---------- */}
        <section className={styles.monitor} aria-label="Project index">
          <div className={styles.monTag}>MON-A // INDEX</div>
          <div className={styles.tabs}>
            {(['all', 'research', 'code'] as Filter[]).map((f) => (
              <span key={f} className={filter === f ? styles.tabOn : styles.tab}>
                {f.toUpperCase()}
              </span>
            ))}
          </div>

          <ol className={styles.list}>
            {projects === null && !offline ? (
              <li className={styles.hint}>Loading archive…</li>
            ) : null}
            {projects !== null && visible.length === 0 ? (
              <li className={styles.hint}>Nothing on file yet. Say “Jarvis, research …”.</li>
            ) : null}
            {visible.map((p, i) => (
              <li key={p.id} className={p.id === selectedId ? styles.rowOn : styles.row}>
                <span className={styles.rowNum}>{pad(i + 1)}</span>
                <span className={styles.rowBody}>
                  <span className={styles.rowTitle}>{p.title}</span>
                  <span className={styles.rowMeta}>
                    <i className={p.kind === 'code' ? styles.kindCode : styles.kindResearch}>
                      {p.kind === 'code' ? 'CODE' : 'RESEARCH'}
                    </i>
                    <span>{ago(p.created_at, now)}</span>
                  </span>
                  {p.status === 'running' && p.kind === 'research' ? (
                    <ResearchBar pct={p.progress ?? 0} stage={p.stage} compact />
                  ) : null}
                </span>
                <span className={styles[`st_${p.status}`]}>{STATUS_LABEL[p.status]}</span>
              </li>
            ))}
          </ol>

          <div className={styles.console} aria-live="polite">
            <div className={styles.consoleHead}>
              <span className={latest ? styles.micHot : styles.mic} aria-hidden />
              VOICE CONSOLE
            </div>
            <ul className={styles.heard}>
              {heard.length === 0 ? (
                <li className={styles.heardIdle}>Awaiting your command, Sir.</li>
              ) : (
                heard.map((h, i) => (
                  <li key={h.at + h.text} className={i === 0 ? styles.heardNew : undefined}>
                    “{h.text}”
                  </li>
                ))
              )}
            </ul>
            <div className={styles.phrases}>
              {PHRASES.map((ph) => (
                <span key={ph}>{ph}</span>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- MONITOR B: sheet ---------- */}
        <section className={styles.monitor} aria-label="Project sheet">
          <div className={styles.monTag}>
            MON-B // {openDoc ? 'DOCUMENT' : meta ? 'DOSSIER' : 'STANDBY'}
          </div>
          {latest ? <div className={styles.voiceNote}>◉ HEARD · {latest.text}</div> : null}

          {!meta ? (
            <div className={styles.empty}>
              <Reticle />
            </div>
          ) : (
            <div className={styles.sheetWrap}>
              <div className={styles.dossier}>
                <div className={styles.dossierHead}>
                  <div>
                    <span className={styles.idTag}>{meta.id}</span>
                    <h1>{meta.title}</h1>
                  </div>
                  <span className={styles[`st_${meta.status}`]}>{STATUS_LABEL[meta.status]}</span>
                </div>
                <dl className={styles.specs}>
                  <div>
                    <dt>TYPE</dt>
                    <dd>{meta.kind === 'code' ? 'CODING' : 'RESEARCH'}</dd>
                  </div>
                  <div>
                    <dt>ENGINE</dt>
                    <dd>
                      {meta.model ?? '—'}
                      {meta.variant ? ` · ${meta.variant.toUpperCase()}` : ''}
                    </dd>
                  </div>
                  <div>
                    <dt>OPENED</dt>
                    <dd>{ago(meta.created_at, now)}</dd>
                  </div>
                  {meta.directory ? (
                    <div className={styles.specWide}>
                      <dt>DIRECTORY</dt>
                      <dd>{meta.directory}</dd>
                    </div>
                  ) : null}
                </dl>
                {pendingDelete && pendingDelete.id === meta.id && now - pendingDelete.at < 30000 ? (
                  <div className={styles.deleteBanner} role="alert">
                    <b>CONFIRM ERASURE</b>
                    <span>
                      SAY “CONFIRM DELETE” ·{' '}
                      {Math.max(0, Math.ceil((30000 - (now - pendingDelete.at)) / 1000))}S
                    </span>
                  </div>
                ) : null}
                {meta.status === 'running' && meta.kind === 'research' && !openDoc ? (
                  <ResearchBar pct={meta.progress ?? 0} stage={meta.stage} steps={meta.steps} />
                ) : null}
                {meta.summary && !openDoc ? (
                  <div className={styles.summary}>
                    <span>SUMMARY</span>
                    <p>{meta.summary}</p>
                  </div>
                ) : null}
                {meta.status === 'failed' && meta.error ? (
                  <div className={styles.failure}>{meta.error}</div>
                ) : null}

                <div className={styles.docStrip}>
                  {docs.length === 0 ? (
                    <span className={styles.hint}>
                      {meta.status === 'running'
                        ? 'Documents appear when the engine reports…'
                        : 'No documents.'}
                    </span>
                  ) : (
                    docs.map((d, i) => (
                      <span key={d.name} className={i === docIndex ? styles.docOn : styles.doc}>
                        <b>DOC {pad(i + 1)}</b>
                        <span>{d.title ?? d.name}</span>
                      </span>
                    ))
                  )}
                  {meta.status === 'running' ? (
                    <span className={styles.abort}>SAY “ABORT” TO CANCEL</span>
                  ) : null}
                </div>
              </div>

              {openDoc ? (
                <div className={styles.viewer}>
                  <div className={styles.viewerBar}>
                    <span>
                      DOC {pad((docIndex ?? 0) + 1)} / {pad(docs.length)} · {openDoc.name}
                    </span>
                    <span className={scrolling ? styles.scrollOn : styles.scrollBtn}>
                      {scrolling ? `AUTO-SCROLL ▸ ${speed.toUpperCase()}` : 'AUTO-SCROLL ‖'}
                    </span>
                  </div>
                  <div className={styles.viewerBody}>
                    <div ref={sheetRef} className={styles.sheet} onScroll={onSheetScroll}>
                      {openDoc.name.endsWith('.log') ? (
                        <pre className={styles.log}>{openDoc.text}</pre>
                      ) : (
                        <Markdown text={openDoc.text} />
                      )}
                    </div>
                    <div className={styles.ruler} aria-hidden>
                      <div className={styles.rulerThumb} style={{ top: `${progress * 100}%` }} />
                    </div>
                  </div>
                </div>
              ) : meta.status === 'running' && meta.kind === 'code' && detail?.report ? (
                <pre className={styles.liveLog}>{detail.report.slice(-4000)}</pre>
              ) : null}
            </div>
          )}
        </section>
      </main>

      <footer className={styles.footer}>
        <span>VOICE CONTROLLED · say “Jarvis” then any command on the left console</span>
        <span>{scrolling ? `AUTO-SCROLL ${speed.toUpperCase()}` : 'STANDING BY'}</span>
      </footer>
    </div>
  );
}

/** Live research progress: amber bar that fills as the engine searches,
 *  reads and writes. `compact` is the one-line version for index rows. */
function ResearchBar({
  pct,
  stage,
  steps,
  compact = false,
}: {
  pct: number;
  stage?: string;
  steps?: number;
  compact?: boolean;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(pct)));
  return (
    <span
      className={compact ? styles.barCompact : styles.bar}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`Research ${clamped}% · ${stage ?? 'starting'}`}
    >
      {!compact ? (
        <span className={styles.barHead}>
          <b>RESEARCH IN PROGRESS</b>
          <span>
            {typeof steps === 'number' ? `${steps} WEB STEP${steps === 1 ? '' : 'S'} · ` : ''}
            {pad(clamped)}%
          </span>
        </span>
      ) : null}
      <span className={styles.barTrack}>
        <span className={styles.barFill} style={{ width: `${clamped}%` }} />
      </span>
      <span className={styles.barStage}>
        {compact ? `${clamped}% · ` : ''}
        {stage ?? 'Starting'}
      </span>
    </span>
  );
}
