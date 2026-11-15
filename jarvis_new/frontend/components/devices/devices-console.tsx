'use client';

/** Connected Devices: status board for Jarvis Fleet (src/fleet.py, bridge /fleet/*).
 *
 *  VOICE-ONLY: nothing here is a control. Voice (or any shell code) drives the
 *  page with a window event:
 *
 *    window.dispatchEvent(new CustomEvent('jarvis:devices', {
 *      detail: {
 *        action: 'select' | 'ping' | 'ask' | 'approve' | 'deny',
 *        device?: string, text?: string, account?: string, name?: string,
 *      },
 *    }));
 *
 *    select   selects `device` (no network).
 *    ping     POST /fleet/ping {device}; the round trip is timed here.
 *    ask      POST /fleet/ask {device, text}; the reply shows in LAST REPLY.
 *    approve  POST /fleet/approve {account?, name?}; a pending caller is trusted.
 *    deny     POST /fleet/deny {account?, name?}; removes the pending caller.
 *
 *  Without `device`, ping and ask act on the selected device. `approve` and
 *  `deny` need `account` or `name` (a pending node name or an account). `?device=<name>`
 *  in the URL selects a device on load. Data: /fleet/devices every 5 s and
 *  /fleet/share every 60 s. Approve and deny refresh /fleet/devices at once.
 *  When the bridge is unreachable or answers ok:false, the page shows
 *  BRIDGE OFFLINE and keeps the last known list marked stale. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AsciiBox, GlyphLamp } from '@/components/ascii/glyphs';
import { Decode } from '@/components/ascii/use-decode';
import { useSurfaceVisibility } from '@/hooks/hud/use-surface-visibility';
import {
  type FleetDevice,
  type FleetDevicesReply,
  type FleetShare,
  bridgeFleetApprove,
  bridgeFleetAsk,
  bridgeFleetDeny,
  bridgeFleetDevices,
  bridgeFleetPing,
  bridgeFleetShare,
} from '@/lib/bridge';
import { startVisiblePoll } from '@/lib/visible-poll';
import {
  DeviceGroup,
  FLEET_EVENT,
  type FleetEventDetail,
  type FleetReply,
  LastReplyPanel,
  PendingPanel,
  type PingRecord,
  SelectedPanel,
  SharePanel,
  VoiceHints,
  resolveDevice,
} from './devices-panels';

const DEVICES_MS = 5000;
const SHARE_MS = 60000;
const OFFLINE = 'BRIDGE OFFLINE';

const stamp = () => new Date().toLocaleTimeString([], { hour12: false });

export function DevicesConsole() {
  useSurfaceVisibility();

  const [fleet, setFleet] = useState<FleetDevicesReply | null>(null);
  const [link, setLink] = useState<'unknown' | 'live' | 'offline'>('unknown');
  const [offlineReason, setOfflineReason] = useState('');
  const [polledAt, setPolledAt] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(0);
  const [share, setShare] = useState<FleetShare | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [pings, setPings] = useState<Record<string, PingRecord>>({});
  const [reply, setReply] = useState<FleetReply | null>(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const devices = useMemo<FleetDevice[]>(() => fleet?.devices ?? [], [fleet]);
  const pending = useMemo(() => fleet?.pending ?? [], [fleet]);
  const selected = devices.find((d) => d.name === selectedName) ?? devices[0] ?? null;
  const own = useMemo(() => devices.filter((d) => !d.partner), [devices]);
  const partner = useMemo(() => devices.filter((d) => d.partner), [devices]);
  const onlineCount = devices.filter((d) => d.online === true).length;

  // Latest values for the window listener, which is registered once.
  const devicesRef = useRef<FleetDevice[]>(devices);
  const selectedRef = useRef<string | null>(null);
  useEffect(() => {
    devicesRef.current = devices;
    selectedRef.current = selected?.name ?? null;
  });

  // Monotonic id so an older command never clears a newer busy label.
  const opSeq = useRef(0);

  // Apply one /fleet/devices reply (null = bridge offline).
  const applyDevices = useCallback((res: FleetDevicesReply | null) => {
    if (res && res.ok === true) {
      setFleet(res);
      setLink('live');
      setOfflineReason('');
      setPolledAt(stamp());
      setNowMs(Date.now());
    } else {
      setLink('offline');
      setOfflineReason(res?.error ?? 'no reply from the bridge');
    }
  }, []);

  const runPing = useCallback(async (name: string) => {
    const id = ++opSeq.current;
    setBusy(`PINGING ${name}`);
    const t0 = performance.now();
    const res = await bridgeFleetPing(name);
    const ms = Math.round(performance.now() - t0);
    const ok = res?.ok === true;
    const error = ok ? undefined : (res?.error ?? OFFLINE);
    const at = stamp();
    setPings((p) => ({ ...p, [name]: { ok, ms, error, at } }));
    setReply({
      kind: 'ping',
      device: name,
      command: 'ping',
      ok,
      body: ok ? `${name} answered in ${ms} ms.` : (error ?? OFFLINE),
      at,
    });
    if (opSeq.current === id) setBusy(null);
  }, []);

  const runAsk = useCallback(async (name: string, text: string) => {
    const id = ++opSeq.current;
    setBusy(`ASKING ${name}`);
    const res = await bridgeFleetAsk(name, text);
    const ok = res?.ok === true;
    const body = ok ? res?.reply?.trim() || 'No reply text.' : (res?.error ?? OFFLINE);
    setReply({ kind: 'ask', device: name, command: text, ok, body, at: stamp() });
    if (opSeq.current === id) setBusy(null);
  }, []);

  const runDecide = useCallback(
    async (kind: 'approve' | 'deny', detail: { account?: string; name?: string }) => {
      const label = detail.name ?? detail.account ?? '';
      const id = ++opSeq.current;
      setBusy(`${kind === 'approve' ? 'APPROVING' : 'DENYING'} ${label}`);
      const res =
        kind === 'approve' ? await bridgeFleetApprove(detail) : await bridgeFleetDeny(detail);
      const ok = res?.ok === true;
      const body = ok
        ? kind === 'approve'
          ? `${label} approved.`
          : `${label} denied.`
        : (res?.error ?? OFFLINE);
      setReply({ kind, device: label, command: `${kind} ${label}`, ok, body, at: stamp() });
      if (opSeq.current === id) setBusy(null);
      // Show the new trust state at once, not on the next poll.
      applyDevices(await bridgeFleetDevices());
    },
    [applyDevices]
  );

  // Device list: poll while visible.
  useEffect(
    () =>
      startVisiblePoll(async (signal) => {
        const res = await bridgeFleetDevices();
        if (signal.aborted) return;
        applyDevices(res);
      }, DEVICES_MS),
    [applyDevices]
  );

  // Sharing steps: slow poll, they rarely change.
  useEffect(
    () =>
      startVisiblePoll(async (signal) => {
        const res = await bridgeFleetShare();
        if (!signal.aborted && res && res.ok === true) setShare(res);
      }, SHARE_MS),
    []
  );

  // `?device=<name>` selects on load (read once, applied when the list arrives).
  const urlDevice = useRef<string | null>(null);
  useEffect(() => {
    urlDevice.current = new URLSearchParams(window.location.search).get('device');
  }, []);
  useEffect(() => {
    const want = urlDevice.current;
    if (!want || devices.length === 0) return;
    urlDevice.current = null;
    const hit = resolveDevice(want, devices);
    if (hit) setSelectedName(hit.name);
    else setNotice(`no device named ${want}`);
  }, [devices]);

  // Voice contract: window event `jarvis:devices` (see the file header).
  useEffect(() => {
    const onEvent = (ev: Event) => {
      const d = (ev as CustomEvent<FleetEventDetail>).detail;
      if (!d || typeof d !== 'object') return;
      if (d.action === 'approve' || d.action === 'deny') {
        const account = typeof d.account === 'string' ? d.account.trim() : '';
        const name = typeof d.name === 'string' ? d.name.trim() : '';
        if (!account && !name) {
          setNotice(`${d.action} needs a pending device name or account`);
          return;
        }
        setNotice('');
        void runDecide(d.action, {
          ...(account ? { account } : {}),
          ...(name ? { name } : {}),
        });
        return;
      }
      if (d.action !== 'select' && d.action !== 'ping' && d.action !== 'ask') {
        setNotice(`unknown devices action: ${String(d.action)}`);
        return;
      }
      const list = devicesRef.current;
      const named = typeof d.device === 'string' && d.device.trim() !== '';
      const target = named
        ? resolveDevice(d.device as string, list)
        : (list.find((x) => x.name === selectedRef.current) ?? null);
      if (!target) {
        setNotice(named ? `no device named ${d.device}` : 'no device selected');
        return;
      }
      setSelectedName(target.name);
      setNotice('');
      if (d.action === 'ping') void runPing(target.name);
      if (d.action === 'ask') {
        const text = typeof d.text === 'string' ? d.text.trim() : '';
        if (!text) setNotice('ask needs the command text');
        else void runAsk(target.name, text);
      }
    };
    window.addEventListener(FLEET_EVENT, onEvent);
    return () => window.removeEventListener(FLEET_EVENT, onEvent);
  }, [runPing, runAsk, runDecide]);

  const offline = link === 'offline';

  return (
    <main className="relative min-h-dvh overflow-x-hidden overflow-y-auto bg-[#03101a] p-3 font-mono text-[#d9f4ff] [color-scheme:dark] sm:p-4">
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_35%,rgba(10,42,68,0.6),transparent_70%)]"
      />
      <div className="relative mx-auto flex max-w-[1400px] flex-col gap-3">
        <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 border-b border-[rgba(95,227,255,0.3)] pb-2">
          <div className="min-w-0">
            <h1 className="m-0 text-[20px] font-bold tracking-[0.2em] text-[#d8f8ff] sm:text-[24px]">
              <Decode text="CONNECTED DEVICES" ms={600} />
            </h1>
            <div className="mt-0.5 text-[11px] tracking-[0.14em] text-[#7fa6b8] uppercase">
              Jarvis Fleet · Tailscale
              {fleet?.self ? ` · this device ${fleet.self}` : ''}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] tracking-[0.12em] uppercase">
            <span className="flex items-center gap-1.5">
              <GlyphLamp state={offline ? 'warn' : link === 'live' ? 'on' : 'off'} />
              {offline ? OFFLINE : link === 'live' ? 'bridge online' : 'connecting'}
            </span>
            <span className="text-[#7fa6b8]">own {own.length}</span>
            <span className="text-[#ffb347]">partner {partner.length}</span>
            {pending.length > 0 ? (
              <span className="text-[#ffb347]">pending {pending.length}</span>
            ) : null}
            <span className="text-[#7fa6b8]">
              online {onlineCount}/{devices.length}
            </span>
            <span className="text-[#46677a]">
              {fleet?.account ? `account ${fleet.account}` : ''}
              {polledAt ? ` · polled ${polledAt}` : ''}
            </span>
          </div>
        </header>

        {offline ? (
          <div
            role="status"
            className="border border-[#ffb347] bg-[rgba(255,179,71,0.08)] px-3 py-2 text-[12px] text-[#ffb347]"
          >
            <span className="font-bold tracking-[0.2em]">■ {OFFLINE}</span>
            <span className="ml-3 break-words text-[#d9f4ff]">
              {offlineReason || 'no reply'}
              {polledAt ? ` · last list ${polledAt}, shown as stale` : ''}
            </span>
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-3 min-[900px]:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <AsciiBox label="Fleet grid" index={1} aside={`${devices.length} devices`} heavy>
            {devices.length === 0 ? (
              <p className="m-0 text-[13px] text-[#7fa6b8]">
                {offline
                  ? 'No device list: the bridge is offline.'
                  : link === 'live'
                    ? 'No devices paired or shared yet.'
                    : 'Reading devices from the bridge…'}
              </p>
            ) : (
              <div className={offline ? 'opacity-60' : undefined}>
                <DeviceGroup
                  title="OWN ACCOUNT"
                  devices={own}
                  selectedName={selected?.name ?? null}
                  pings={pings}
                />
                <DeviceGroup
                  title="PARTNER ACCOUNT · SHARED IN"
                  devices={partner}
                  selectedName={selected?.name ?? null}
                  pings={pings}
                />
              </div>
            )}
          </AsciiBox>

          <div className="flex min-w-0 flex-col gap-3">
            <SelectedPanel
              device={selected}
              ping={selected ? pings[selected.name] : undefined}
              notice={notice}
              selfName={fleet?.self ?? ''}
            />
            <LastReplyPanel reply={reply} busy={busy} selectedName={selected?.name ?? null} />
            <PendingPanel pending={pending} nowMs={nowMs} />
          </div>

          <div className="min-w-0">
            <SharePanel share={share} offline={offline} />
          </div>
          <div className="min-w-0">
            <VoiceHints
              selectedName={selected?.name ?? null}
              pendingNode={pending[0]?.node || null}
            />
          </div>
        </div>
      </div>
    </main>
  );
}
