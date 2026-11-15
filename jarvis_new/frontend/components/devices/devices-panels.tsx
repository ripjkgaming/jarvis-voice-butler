'use client';

import { type ReactNode, memo } from 'react';
import { AsciiBox, GlyphLamp } from '@/components/ascii/glyphs';
import type { FleetDevice, FleetPending, FleetShare } from '@/lib/bridge';

/** Window event that voice (or any shell code) uses to drive this page. */
export const FLEET_EVENT = 'jarvis:devices';

export type FleetEventDetail = {
  action: 'select' | 'ping' | 'ask' | 'approve' | 'deny';
  /** Device name as listed by /fleet/devices (spoken names are matched loosely). */
  device?: string;
  /** Command text, required for `ask`. */
  text?: string;
  /** Account to approve or deny (`approve` / `deny` only). */
  account?: string;
  /** Pending node name or account to approve or deny (`approve` / `deny` only). */
  name?: string;
};

/** Last measured `POST /fleet/ping` round trip through the bridge. */
export type PingRecord = { ok: boolean; ms: number; error?: string; at: string };

/** Last reply from a fleet command (ping, ask, approve or deny). */
export type FleetReply = {
  kind: 'ping' | 'ask' | 'approve' | 'deny';
  device: string;
  command: string;
  ok: boolean;
  body: string;
  at: string;
};

/** Stable device key, the same rule as `slug()` in src/fleet.py. */
export function slugName(spoken: string): string {
  return spoken
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
}

/** Exact match first, then a unique prefix of 3+ characters ("office" → office-mac). */
export function resolveDevice(spoken: string, devices: FleetDevice[]): FleetDevice | null {
  const key = slugName(spoken);
  if (!key) return null;
  const exact = devices.find((d) => d.name === key);
  if (exact) return exact;
  if (key.length < 3) return null;
  const hits = devices.filter((d) => d.name.startsWith(key));
  return hits.length === 1 ? hits[0] : null;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url || 'no address';
  }
}

/** Link state of one device, from real fields only (see FleetDevice). */
export type LinkState = 'linked' | 'awaiting' | 'nojarvis' | 'offline' | 'unknown';

export function linkStateOf(d: FleetDevice): LinkState {
  if (d.jarvis === true) {
    if (d.trusted === true) return 'linked';
    if (d.trusted === false) return 'awaiting';
    return 'unknown';
  }
  if (d.online === true) return 'nojarvis';
  if (d.online === false) return 'offline';
  return 'unknown';
}

const LINK_VIEW: Record<LinkState, { label: string; lamp: 'on' | 'warn' | 'off' }> = {
  linked: { label: 'LINKED', lamp: 'on' },
  awaiting: { label: 'AWAITING APPROVAL', lamp: 'warn' },
  nojarvis: { label: 'NO JARVIS', lamp: 'warn' },
  offline: { label: 'OFFLINE', lamp: 'off' },
  unknown: { label: 'UNKNOWN', lamp: 'warn' },
};

const LINK_HINT: Partial<Record<LinkState, string>> = {
  awaiting: 'waiting for approval on that device',
  nojarvis: 'install Jarvis fleet here',
};

/** Relative age of an ISO timestamp, e.g. "3 min ago". Raw text if unparseable. */
export function ageLabel(iso: string, nowMs: number): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso || 'unknown';
  const s = Math.max(0, Math.round((nowMs - then) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

const TAG =
  'inline-flex items-center border px-1.5 py-px text-[10px] leading-4 tracking-[0.12em] uppercase';

export const DeviceTile = memo(function DeviceTile({
  device,
  selected,
  ping,
}: {
  device: FleetDevice;
  selected: boolean;
  ping?: PingRecord;
}) {
  const link = linkStateOf(device);
  const view = LINK_VIEW[link];
  const hint = LINK_HINT[link];
  return (
    <li
      aria-current={selected ? 'true' : undefined}
      data-partner={device.partner ? 'true' : undefined}
      data-selected={selected ? 'true' : undefined}
      data-link={link}
      className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-1 border-b border-[rgba(95,227,255,0.12)] py-2.5 last:border-b-0 data-[selected=true]:bg-[rgba(95,227,255,0.07)]"
    >
      <span
        aria-hidden="true"
        className={`text-center leading-5 ${device.partner ? 'text-[#ffb347]' : 'text-[#5fe3ff]'}`}
      >
        {selected ? '▶' : '┃'}
      </span>
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <span className="min-w-0 text-[15px] font-bold break-all text-[#d8f8ff]">
            {device.name}
          </span>
          <span className="flex items-center gap-1.5 text-[11px] text-[#7fa6b8]">
            <GlyphLamp state={view.lamp} />
            {view.label}
          </span>
        </div>
        <div className="mt-0.5 text-[11px] break-all text-[#7fa6b8]">
          {device.os || 'os unknown'} · {hostOf(device.url)} ·{' '}
          {device.paired ? 'paired' : 'not paired'}
        </div>
        <div className="mt-0.5 text-[11px] break-all text-[#46677a]">
          account {device.account || 'unknown'}
        </div>
        {hint ? <div className="mt-0.5 text-[11px] text-[#ffb347]">{hint}</div> : null}
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {device.partner ? (
            <span className={`${TAG} border-[#ffb347] text-[#ffb347]`}>Partner account</span>
          ) : (
            <span className={`${TAG} border-[#5fe3ff] text-[#5fe3ff]`}>Own account</span>
          )}
          {device.shared ? (
            <span className={`${TAG} border-[#ffb347]/60 text-[#ffb347]/90`}>Shared</span>
          ) : null}
          {ping ? (
            <span
              className={`${TAG} ${ping.ok ? 'border-[#5fe3ff]/50 text-[#d8f8ff]' : 'border-[#ff4d5e] text-[#ff4d5e]'}`}
              title="Round trip of POST /fleet/ping through the bridge"
            >
              {ping.ok ? `RTT ${ping.ms} ms` : 'Ping failed'}
            </span>
          ) : null}
        </div>
      </div>
    </li>
  );
});

export function DeviceGroup({
  title,
  devices,
  selectedName,
  pings,
}: {
  title: string;
  devices: FleetDevice[];
  selectedName: string | null;
  pings: Record<string, PingRecord>;
}) {
  if (devices.length === 0) return null;
  return (
    <div>
      <div className="mt-2 mb-1 text-[10px] tracking-[0.18em] text-[#46677a] uppercase first:mt-0">
        ── {title} ({devices.length})
      </div>
      <ul className="m-0 list-none p-0">
        {devices.map((d) => (
          <DeviceTile
            key={d.name}
            device={d}
            selected={d.name === selectedName}
            ping={pings[d.name]}
          />
        ))}
      </ul>
    </div>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-2 border-b border-[rgba(95,227,255,0.1)] py-1 text-[12px]">
      <span className="text-[10px] tracking-[0.14em] text-[#46677a] uppercase">{k}</span>
      <span className="min-w-0 break-all text-[#d9f4ff]">{children}</span>
    </div>
  );
}

export function SelectedPanel({
  device,
  ping,
  notice,
  selfName,
}: {
  device: FleetDevice | null;
  ping?: PingRecord;
  notice: string;
  selfName: string;
}) {
  return (
    <AsciiBox label="Selected device" index={2} aside={device?.partner ? 'partner' : 'own'}>
      {device ? (
        <div className="text-[#d9f4ff]">
          <div className="mb-1 text-[18px] font-bold break-all text-[#d8f8ff]">{device.name}</div>
          <Row k="State">
            <span className="inline-flex items-center gap-1.5">
              <GlyphLamp
                state={device.online === true ? 'on' : device.online === false ? 'off' : 'warn'}
              />
              {device.online === true ? 'online' : device.online === false ? 'offline' : 'unknown'}
            </span>
          </Row>
          <Row k="Link">{LINK_VIEW[linkStateOf(device)].label}</Row>
          <Row k="OS">{device.os || 'unknown'}</Row>
          <Row k="Tailnet address">{device.url || 'none'}</Row>
          <Row k="Account">{device.account || 'unknown'}</Row>
          <Row k="Source">
            {device.partner ? 'partner account (shared node)' : 'own account'}
            {device.shared ? ', shared' : ''}
          </Row>
          <Row k="Pairing">{device.paired ? 'paired (token stored)' : 'not paired'}</Row>
          <Row k="Last ping">
            {ping
              ? ping.ok
                ? `${ping.ms} ms round trip · ${ping.at}`
                : `failed · ${ping.error ?? 'no reply'} · ${ping.at}`
              : 'not pinged yet'}
          </Row>
        </div>
      ) : (
        <p className="m-0 text-[#7fa6b8]">
          No device selected. {selfName ? `This computer is ${selfName}.` : ''}
        </p>
      )}
      <p aria-live="polite" className="mt-2 min-h-4 text-[11px] text-[#ffb347]">
        {notice}
      </p>
    </AsciiBox>
  );
}

export function LastReplyPanel({
  reply,
  busy,
  selectedName,
}: {
  reply: FleetReply | null;
  busy: string | null;
  selectedName: string | null;
}) {
  return (
    <AsciiBox label="Last reply" index={3} aside={busy ?? (reply ? reply.kind : 'idle')} heavy>
      <div aria-live="polite" className="min-h-[5rem] text-[13px] leading-relaxed">
        {reply ? (
          <>
            <div className="mb-1.5 text-[11px] break-all text-[#7fa6b8]">
              {reply.kind.toUpperCase()} {reply.device} · {reply.at}
            </div>
            {reply.kind === 'ask' ? (
              <div className="mb-1.5 break-words text-[#5fe3ff]">› {reply.command}</div>
            ) : null}
            <div
              className={`max-h-[16rem] overflow-x-hidden overflow-y-auto break-words whitespace-pre-wrap ${reply.ok ? 'text-[#d9f4ff]' : 'text-[#ff4d5e]'}`}
            >
              {reply.body}
            </div>
          </>
        ) : (
          <p className="m-0 text-[#7fa6b8]">
            No fleet command yet this session.
            {selectedName ? ` Selected: ${selectedName}.` : ''}
          </p>
        )}
      </div>
    </AsciiBox>
  );
}

export function PendingPanel({ pending, nowMs }: { pending: FleetPending[]; nowMs: number }) {
  return (
    <AsciiBox
      label="Pending approvals"
      index={4}
      aside={pending.length > 0 ? `${pending.length} waiting` : undefined}
    >
      {pending.length === 0 ? (
        <p className="m-0 text-[13px] text-[#7fa6b8]">No pending requests.</p>
      ) : (
        <ul className="m-0 list-none space-y-2 p-0">
          {pending.map((p, i) => {
            const who = p.node || p.account || 'unnamed';
            return (
              <li
                key={`${i}-${p.ip}-${who}`}
                className="border-b border-[rgba(95,227,255,0.12)] pb-2 text-[12px] last:border-b-0"
              >
                <div className="text-[14px] font-bold break-all text-[#d8f8ff]">{who}</div>
                <div className="text-[11px] break-all text-[#7fa6b8]">
                  account {p.account || 'unknown'} · last seen {ageLabel(p.last_seen, nowMs)}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                  <span>
                    <span className="text-[#ffb347]">SAY:</span>{' '}
                    <span className="text-[#d9f4ff]">“approve {who}”</span>
                  </span>
                  <span>
                    <span className="text-[#ffb347]">SAY:</span>{' '}
                    <span className="text-[#d9f4ff]">“deny {who}”</span>
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </AsciiBox>
  );
}

export function SharePanel({ share, offline }: { share: FleetShare | null; offline: boolean }) {
  const steps = share?.steps ?? [];
  return (
    <AsciiBox label="Share this device" index={5} aside={share?.account ? 'tailnet' : undefined}>
      {share ? (
        <div className="text-[12px] text-[#d9f4ff]">
          {share.account ? (
            <div className="mb-2 break-all text-[#7fa6b8]">
              Signed in as {share.account}
              {share.partners ? ` · ${share.partners.length} partner account(s)` : ''}
            </div>
          ) : null}
          {steps.length > 0 ? (
            <ol className="m-0 list-none space-y-1.5 p-0">
              {steps.map((s, i) => (
                <li key={i} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-1">
                  <span className="text-[#ffb347]">{String(i + 1).padStart(2, '0')}</span>
                  <span className="break-words">{s}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="m-0 text-[#7fa6b8]">The bridge sent no sharing steps.</p>
          )}
        </div>
      ) : (
        <p className="m-0 text-[#7fa6b8]">
          {offline ? 'Sharing steps unavailable while the bridge is offline.' : 'Loading steps…'}
        </p>
      )}
    </AsciiBox>
  );
}

export function VoiceHints({
  selectedName,
  pendingNode,
}: {
  selectedName: string | null;
  pendingNode: string | null;
}) {
  const who = selectedName ?? '<device>';
  const waiting = pendingNode ?? '<node>';
  const hints = [
    'list my devices',
    'who wants to connect',
    `approve ${waiting}`,
    `deny ${waiting}`,
    `ping ${who}`,
    `ask ${who} to lock the screen`,
    `tell ${who} to open spotify`,
  ];
  return (
    <AsciiBox label="Voice" index={6} wipe={false}>
      <ul className="m-0 grid list-none gap-1 p-0 text-[12px] sm:grid-cols-2">
        {hints.map((h) => (
          <li key={h} className="break-words text-[#7fa6b8]">
            <span className="text-[#ffb347]">SAY:</span>{' '}
            <span className="text-[#d9f4ff]">“{h}”</span>
          </li>
        ))}
      </ul>
    </AsciiBox>
  );
}
