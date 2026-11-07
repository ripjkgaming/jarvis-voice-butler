/** #stage: the strip, the three always-mounted pages, the toast, and the
 * glue between store, motion, gate flows, controls and the call. */
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { api, ApiError } from './api';
import { runAction } from './controls';
import { buzz, HAPTIC } from './motion/haptics';
import type { PageId } from './motion/page-morph';
import { Core } from './pages/Core';
import { Deck } from './pages/Deck';
import { Gate, type GateMode } from './pages/Gate';
import { browserPoller } from './poll';
import {
  actions,
  auth,
  call,
  canControl,
  captions,
  clock,
  laptop,
  link,
  loadPersisted,
  navWord,
  page,
  savePersisted,
  showToast,
  sw,
  type ControlAction,
} from './store';
import { openAudio } from './voice/audio';
import { CallMachine, pushCaption } from './voice/call';
import { wsUrl } from './voice/ws';
import type { Geo } from './ui/Reactor';
const geometry = (): Geo => ({ W: window.innerWidth, H: window.innerHeight, R: 0 });
import { StatusStrip } from './ui/StatusStrip';
import { Toast } from './ui/Toast';
import { assertion, registerPasskey } from './webauthn';

type Reg = { reg: ServiceWorkerRegistration | null };
export const swReg: Reg = { reg: null };

const stageEl = () => document.getElementById('stage');
const q = (sel: string) => stageEl()?.querySelector<HTMLElement>(sel) ?? null;

function deviceName() {
  const ua = navigator.userAgent;
  const m = ua.match(/Android [\d.]+; ([^;)]+)/);
  return (m?.[1] || 'phone').replace(/[^\w .-]/g, '').slice(0, 40);
}

export function App({ pairCode }: { pairCode: string | null }) {
  const [geo, setGeo] = useState<Geo>(geometry());
  const [mode, setMode] = useState<GateMode>('probe');
  const [row, setRow] = useState('LOCKE//LINK // HANDSHAKE');
  const [err, setErr] = useState<{ text: string; tone: 'fail' | 'info' }>({ text: '', tone: 'info' });
  const [busy, setBusy] = useState(false);
  const [powerOpen, setPowerOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const errRef = useRef<HTMLDivElement | null>(null);
  const deckEntry = useRef(false);
  const suppressPop = useRef(false);
  const lockTimer = useRef<number>(0);
  const triedRegister = useRef(false);
  const pkFlag = useRef(!!loadPersisted().pk);

  // Navigation changes immediately, without a render loop or visual effects.
  const morph = useRef({ current: 'gate' as PageId, target: 'gate' as PageId }).current;
  function remeasure() {}
  function goTo(p: PageId) {
    morph.current = p;
    morph.target = p;
    page.value = { current: p, target: p };
    if (p !== 'gate') savePersisted({ page: p });
    stageEl()?.querySelectorAll<HTMLElement>('[data-page]').forEach((el) => {
      const hidden = el.dataset.page !== p;
      el.toggleAttribute('data-hidden', hidden);
      el.inert = hidden;
    });
    q(`[data-page="${p}"] [data-first]`)?.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------ navigation
  function toDeck() {
    if (auth.value.gate !== 'open' || morph.target === 'deck' || morph.target === 'gate') return;
    buzz(HAPTIC.tap);
    history.pushState({ p: 'deck' }, '');
    deckEntry.current = true;
    goTo('deck');
  }
  function toCore() {
    if (morph.target !== 'deck') return;
    buzz(HAPTIC.tap);
    if (deckEntry.current) {
      history.back(); // popstate plays the reverse morph
      return;
    }
    goTo('core');
  }
  function nav(w: 'deck' | 'core') {
    if (w === 'deck') toDeck();
    else toCore();
  }

  useEffect(() => {
    const onPop = () => {
      if (suppressPop.current) {
        suppressPop.current = false;
        return;
      }
      deckEntry.current = false;
      if (morph.target === 'deck') goTo('core');
    };
    addEventListener('popstate', onPop);
    return () => removeEventListener('popstate', onPop);
  }, []);

  // ------------------------------------------------------------- polling
  const poller = useMemo(() => browserPoller((kind) => toGate(kind)), []);

  function toGate(kind: 'auth' | 'gate', why?: string) {
    poller.stop();
    if (kind === 'auth') {
      setMode('unpaired');
      setRow('UNPAIRED // RUN phone_pair ON HOST');
    } else {
      const soft = auth.value.gate === 'soft';
      setMode(soft ? 'soft' : 'cold');
      setRow(soft ? 'GATE SOFT // FINGERPRINT OR PASSPHRASE' : 'GATE SEALED // PASSPHRASE');
    }
    setErr({ text: why ?? '', tone: 'info' });
    if (deckEntry.current) {
      deckEntry.current = false;
      suppressPop.current = true;
      history.back();
    }
    if (morph.target !== 'gate') goTo('gate');
  }

  async function openGate() {
    buzz(HAPTIC.gate);
    auth.value = { ...auth.value, paired: true, gate: 'open', triesLeft: null, lockoutUntil: null };
    setErr({ text: '', tone: 'info' });
    if (inputRef.current) inputRef.current.value = '';
    poller.start();
    goTo('core');
    if (!pkFlag.current && !triedRegister.current && navigator.credentials) {
      triedRegister.current = true;
      try {
        const res = await api<{ registration: Record<string, unknown> }>('POST', '/phone/v1/challenge', { purpose: 'register' });
        await registerPasskey(res.registration);
        pkFlag.current = true;
        savePersisted({ pk: true });
        showToast('FINGERPRINT SAVED');
      } catch {
        showToast('FINGERPRINT NOT SAVED · PASSPHRASE STILL WORKS');
      }
    }
  }

  // ---------------------------------------------------------------- gate
  async function probe() {
    const t0 = performance.now();
    try {
      const json = await api<Record<string, unknown>>('GET', '/phone/v1/state');
      link.value = { phase: 'online', rttMs: performance.now() - t0, lastOk: Date.now() };
      void json;
      await openGate();
    } catch (e) {
      const k = (e as ApiError).kind;
      const rtt = performance.now() - t0;
      if (k === 'auth') {
        link.value = { phase: 'online', rttMs: rtt, lastOk: Date.now() };
        if (pairCode) return pair(pairCode);
        toGate('auth');
      } else if (k === 'gate') {
        link.value = { phase: 'online', rttMs: rtt, lastOk: Date.now() };
        toGate('gate');
      } else {
        link.value = { phase: 'offline', rttMs: null, lastOk: null };
        setMode('offline');
        setRow('Cannot reach laptop. Check Tailscale and Jarvis. Retrying…');
            setTimeout(() => void probe(), 4000);
      }
    }
  }

  async function pair(code: string) {
    setMode('pairing');
    setRow('PAIRING // EXCHANGING KEYS');
    try {
      const res = await api<{ device_id: string; registration: Record<string, unknown> }>('POST', '/phone/v1/pair', {
        code,
        name: deviceName(),
      });
      auth.value = { ...auth.value, paired: true };
      setRow(`DEVICE ${res.device_id} // REGISTER FINGERPRINT`);
      setMode('register');
      await register(res.registration);
    } catch (e) {
      const err = e as ApiError;
      const msg =
        err.status === 409
          ? 'DEVICE LIMIT 3 · REVOKE ONE ON HOST'
          : err.status === 429
            ? 'TOO MANY TRIES · WAIT A MINUTE'
            : err.status === 503
              ? 'HOST NOT CONFIGURED · SEE RUNBOOK'
              : err.kind === 'offline'
                ? 'NO LINK TO HOST'
                : 'CODE INVALID OR EXPIRED';
      setMode('unpaired');
      setRow('UNPAIRED // RUN phone_pair ON HOST');
      setErr({ text: msg, tone: 'fail' });
      }
  }

  async function register(options?: Record<string, unknown>) {
    setBusy(true);
    try {
      const opts =
        options ?? (await api<{ registration: Record<string, unknown> }>('POST', '/phone/v1/challenge', { purpose: 'register' })).registration;
      await registerPasskey(opts);
      pkFlag.current = true;
      triedRegister.current = true;
      savePersisted({ pk: true });
      setMode('cold');
      setRow('FINGERPRINT SAVED // ENTER PASSPHRASE');
      setErr({ text: '', tone: 'info' });
    } catch (e) {
      const gateLost = e instanceof ApiError && e.kind === 'gate';
      if (gateLost) {
        setMode('cold');
        setRow('GATE SEALED // PASSPHRASE');
      } else setErr({ text: 'TAP ⌬ TO REGISTER · OR USE PASSPHRASE', tone: 'info' });
    } finally {
      setBusy(false);
      }
  }

  function lockout(seconds: number) {
    clearInterval(lockTimer.current);
    const until = Date.now() + seconds * 1000;
    auth.value = { ...auth.value, lockoutUntil: until, triesLeft: 0 };
    setBusy(true);
    const tick = () => {
      const left = Math.ceil((until - Date.now()) / 1000);
      if (left <= 0) {
        clearInterval(lockTimer.current);
        setBusy(false);
        setErr({ text: 'TRY AGAIN', tone: 'info' });
        return;
      }
      setErr({ text: `LOCKOUT ${left}s`, tone: 'fail' });
    };
    tick();
    lockTimer.current = window.setInterval(tick, 1000);
  }

  function denied(body: Record<string, unknown>) {
    buzz(HAPTIC.failed);
    const left = Number(body.tries_left ?? 0);
    const wait = Number(body.retry_after ?? 0);
    if (inputRef.current) {
      inputRef.current.value = '';
      inputRef.current.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (wait > 0) lockout(wait);
    else setErr({ text: `DENIED · ${left} LEFT`, tone: 'fail' });
  }

  async function submitGate(body: Record<string, unknown>) {
    setBusy(true);
    try {
      await api('POST', '/phone/v1/gate', body);
      setBusy(false);
      await openGate();
    } catch (e) {
      setBusy(false);
      const err = e as ApiError;
      if (err.status === 429) lockout(Number(err.body.retry_after ?? 60));
      else if (err.status === 503) setErr({ text: 'GATE NOT CONFIGURED · RUN deck passwd', tone: 'fail' });
      else if (err.kind === 'auth') toGate('auth');
      else if (err.kind === 'gate') toGate('gate', 'PASSPHRASE NEEDED');
      else if (err.kind === 'offline') setErr({ text: 'NO LINK TO HOST', tone: 'fail' });
      else if (err.status === 401) denied(err.body);
      else setErr({ text: String(err.body.error ?? 'GATE ERROR').toUpperCase().slice(0, 40), tone: 'fail' });
    }
  }

  async function fingerprint() {
    setBusy(true);
    try {
      const proof = await assertion('gate');
      await submitGate({ assertion: proof });
    } catch (e) {
      setBusy(false);
      const err = e as ApiError;
      if (err instanceof ApiError && err.status === 409) setErr({ text: 'NO FINGERPRINT ON FILE · USE PASSPHRASE', tone: 'info' });
      else if (err instanceof ApiError && err.kind === 'gate') toGate('gate', 'PASSPHRASE NEEDED');
      else setErr({ text: 'FINGERPRINT CANCELLED', tone: 'info' });
    }
  }

  // ---------------------------------------------------------------- call
  const wake = useRef<WakeLockSentinel | null>(null);
  const machine = useMemo(
    () =>
      new CallMachine({
        mint: async () => {
          // Bridge payload (mint_token.py): {serverUrl, roomName, participantName, participantToken}.
          // serverUrl is the laptop's loopback hub; the PWA always dials wss://<origin>/voice.
          const r = await api<{ participantToken?: string; roomName?: string }>('POST', '/phone/v1/session', { api_version: 1, mode: 'new' });
          if (!r.participantToken) throw new Error('no token');
          return { token: r.participantToken, room: r.roomName ?? null };
        },
        socket: (url) => new WebSocket(url) as unknown as import('./voice/call').SocketLike,
        url: (token) => wsUrl(location, token),
        audio: openAudio,
        after: (ms, fn) => {
          const id = setTimeout(fn, ms);
          return () => clearTimeout(id);
        },
        now: () => Date.now(),
        onGateClose: () => toGate('gate', 'OWNER REVOKED'),
        onNav: nav,
        haptic: (p) => buzz(p),
        onLive: (live) => void keepAwake(live),
      }),
    [],
  );

  async function keepAwake(live: boolean) {
    try {
      if (live && !wake.current && document.visibilityState === 'visible' && 'wakeLock' in navigator) {
        wake.current = await navigator.wakeLock.request('screen');
        wake.current.addEventListener('release', () => (wake.current = null));
      } else if (!live && wake.current) {
        await wake.current.release();
        wake.current = null;
      }
    } catch {
      /* best effort */
    }
    if ('mediaSession' in navigator) {
      const ms = navigator.mediaSession;
      if (live) {
        ms.metadata = new MediaMetadata({ title: 'LOCKE//LINK', artist: 'call live' });
        ms.playbackState = 'playing';
      } else {
        ms.playbackState = 'none';
      }
      for (const a of ['hangup', 'pause', 'stop'] as const) {
        try {
          ms.setActionHandler(a as MediaSessionAction, live ? () => machine.end() : null);
        } catch {
          /* action unsupported */
        }
      }
    }
  }

  function reactorTap() {
    buzz(HAPTIC.tap);
    const ph = call.value.phase;
    if (ph === 'idle' || ph === 'error') {
      if (!canControl.value) {
        showToast('NO LINK', 'fail');
        return;
      }
      void machine.start();
    } else machine.end();
  }

  // -------------------------------------------------------------- command
  async function command(text: string) {
    const w = navWord(text);
    if (w) return nav(w);
    if (call.value.phase === 'live' && machine.sendText(text)) {
      pushCaption('user', text);
      return;
    }
    pushCaption('user', text);
    const t0 = performance.now();
    const history = captions.value.lines
      .filter((l) => l.final && l.who !== 'system')
      .slice(-11, -1)
      .map((l) => [l.who === 'user' ? 'user' : 'jarvis', l.text]);
    try {
      const r = await api<{ ok: boolean; reply?: string; error?: string; via_route?: boolean; ui?: string }>('POST', '/phone/v1/command', {
        api_version: 1,
        text,
        instant: true,
        history,
      });
      const ms = Math.round(performance.now() - t0);
      const reply = (r.reply || r.error || '').trim();
      if (reply) pushCaption('jarvis', reply, { viaRoute: r.via_route ? ms : undefined, decode: true });
      if (r.ui === 'deck' || r.ui === 'core') nav(r.ui);
      if (!r.ok) showToast(reply || 'COMMAND FAILED', 'fail');
    } catch (e) {
      const err = e as ApiError;
      if (err.kind === 'gate' || err.kind === 'auth') return toGate(err.kind);
      showToast(err.kind === 'offline' ? 'NO LINK' : String(err.body?.error ?? 'COMMAND FAILED').slice(0, 80), 'fail');
    }
  }

  // ------------------------------------------------------------- controls
  function act(a: ControlAction) {
    buzz(HAPTIC.tap);
    if (a !== 'sleep' && a !== 'restart' && a !== 'shutdown') setPowerOpen(false);
    void runAction(a, { applyState: (s) => poller.applyControl(s), refresh: () => poller.refreshNow() });
  }

  // ------------------------------------------------------------ lifecycle
  useEffect(() => {
    const onResize = () => setGeo(geometry());
    addEventListener('resize', onResize);
    goTo('gate');
    void probe();
    const tick = setInterval(() => (clock.value = Date.now()), 1000);
    // Battery telemetry (§0): every 60 s while visible and the gate is open.
    const tele = setInterval(async () => {
      if (document.hidden || !canControl.value || !('getBattery' in navigator)) return;
      try {
        const b = await (navigator as Navigator & { getBattery(): Promise<{ level: number; charging: boolean }> }).getBattery();
        await api('POST', '/phone/telemetry', { battery: Math.round(b.level * 100), charging: b.charging });
      } catch {
        /* telemetry is best effort */
      }
    }, 60000);
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        if (call.value.phase === 'live') void keepAwake(true);
      }
    };
    document.addEventListener('visibilitychange', onVis);
    (window as unknown as { __link: unknown }).__link = { morph, call, laptop, auth, page, actions, captions };
    return () => {
      removeEventListener('resize', onResize);
      document.removeEventListener('visibilitychange', onVis);
      clearInterval(tick);
      clearInterval(tele);
    };
  }, []);

  const lap = laptop.value;
  const now = clock.value;
  const live = call.value.phase !== 'idle' && call.value.phase !== 'error';
  return (
    <div id="stage">
      <StatusStrip
        onUpdate={() => {
          const w = swReg.reg?.waiting;
          if (w) w.postMessage('SKIP_WAITING');
          else location.reload();
        }}
      />
      <Gate
        mode={mode}
        row={row}
        error={err.text}
        errorTone={err.tone}
        busy={busy}
        inputRef={inputRef}
        errRef={errRef}
        onPassphrase={(pw) => {
          if (mode === 'unpaired') {
            // Pairing without the QR: the printed link or the bare code.
            const m = pw.trim().match(/(?:#pair=)?([0-9A-Za-z-]{10,16})\s*$/);
            if (m) void pair(m[1]);
            else setErr({ text: 'CODE INVALID OR EXPIRED', tone: 'fail' });
            return;
          }
          void submitGate({ passphrase: pw });
        }}
        onFingerprint={() => void fingerprint()}
        onRegister={() => void register()}
      />
      <Core
        geo={geo}
        live={live}
        muted={call.value.muted}
        online={canControl.value}
        onReactor={reactorTap}
        onMute={() => {
          if (call.value.phase !== 'live') return;
          machine.toggleMute();
          buzz(HAPTIC.tap);
          showToast(call.value.muted ? 'PHONE MIC MUTED' : 'PHONE MIC LIVE');
        }}
        onNav={toDeck}
        onCommand={command}
      />
      <Deck
        lap={lap}
        host={lap.host ?? '--'}
        now={now}
        can={canControl.value}
        states={actions.value}
        powerOpen={powerOpen}
        onBack={toCore}
        onAction={act}
        onPower={() => {
          buzz(HAPTIC.tap);
          setPowerOpen((o) => !o);
        }}
        onResized={remeasure}
      />
      <Toast />
      <CallStatus />
      <SwHint />
    </div>
  );
}

/** Call errors surface as a toast once. */
function CallStatus() {
  const c = call.value;
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (c.error && c.error !== last.current) showToast(c.error, 'fail');
    last.current = c.error;
  }, [c.error]);
  return null;
}

function SwHint() {
  void sw.value;
  return null;
}
