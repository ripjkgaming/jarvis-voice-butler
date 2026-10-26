/** Protocol 12 network map: pure text layout (no imports, no DOM).
 *
 *  Turns the bridge's `/protocol/state` devices (src/netscan.py) into
 *  ASCII boxes and packs them into a column grid sized to the terminal.
 *  Every line shows a field the scan actually returned: absent, null,
 *  empty or non-finite values produce no row at all, never a
 *  placeholder. Unit-tested by tests/protocol-layout.test.cjs. */

export type ProtocolPhase = 'idle' | 'discovering' | 'scanning' | 'done' | 'error' | 'aborted';

export type ProtocolPort = {
  port?: number;
  proto?: string;
  service?: string;
  product?: string;
  version?: string;
  extra?: string;
  tunnel?: string;
};

export type ProtocolDevice = {
  ip?: string;
  ip6?: string;
  mac?: string;
  vendor?: string;
  hostnames?: string[];
  os?: string;
  osAccuracy?: number | string;
  osAlternatives?: { name?: string; accuracy?: number | string }[];
  type?: string;
  latencyMs?: number;
  uptimeSeconds?: number;
  lastBoot?: string;
  hops?: number;
  ports?: ProtocolPort[];
  isHost?: boolean;
};

export type ProtocolState = {
  ok?: boolean;
  run: number;
  phase: ProtocolPhase;
  progress?: number;
  devices?: ProtocolDevice[];
  host?: ProtocolDevice | null;
  subnet?: string;
  iface?: string;
  mode?: string;
  message?: string;
  notes?: string[];
  startedAt?: number | null;
  finishedAt?: number | null;
  preflight?: { nmap?: boolean; mode?: string; message?: string } | null;
  notice?: { seq?: number; text?: string; ts?: number } | null;
};

/** A body row: `label` (may be '' on continuation rows) and `value`. */
export type BoxRow = { label: string; value: string };

export type DeviceBox = {
  title: string;
  host: boolean;
  ip?: string;
  /** Outer width in cells. */
  width: number;
  /** Full text lines, top border to bottom border, each `width` cells. */
  lines: string[];
  /** The body rows behind lines[1..n-2], for per-part colouring. */
  rows: BoxRow[];
};

export const LABEL_W = 8;
export const HOST_TAG = '[THIS HOST]';
const MIN_BOX = 24;
const MAX_BOX = 58;
const TARGET_BOX = 42;

/** A real value: not absent/null, not blank, not NaN/Infinity. */
function str(v: unknown): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : null;
  if (typeof v !== 'string') return null;
  const t = v.trim();
  return t ? t : null;
}

function num(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function cut(text: string, n: number): string {
  const w = Math.max(0, Math.floor(n));
  if (text.length <= w) return text;
  return w <= 1 ? text.slice(0, w) : text.slice(0, w - 1) + '…';
}

function pad(text: string, n: number): string {
  const t = cut(text, n);
  return t + ' '.repeat(Math.max(0, n - t.length));
}

/** `uptime` seconds as `3d 4h`, `5h 12m`, `7m`, `42s`. */
export function humanUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return h > 0 ? `${d}d ${h}h` : `${d}d`;
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

function pct(v: unknown): string | null {
  const n = num(v);
  return n === null ? null : `${Math.round(n)}%`;
}

/** `443/tcp ssl/https nginx 1.24 (Ubuntu)` from whatever nmap reported. */
export function portLine(p: ProtocolPort): string | null {
  const port = num(p.port);
  if (port === null) return null;
  const proto = str(p.proto);
  const service = str(p.service);
  const tunnel = str(p.tunnel);
  const parts = [proto ? `${port}/${proto}` : String(port)];
  if (service) parts.push(tunnel ? `${tunnel}/${service}` : service);
  const product = str(p.product);
  const version = str(p.version);
  const extra = str(p.extra);
  if (product) parts.push(product);
  if (version) parts.push(version);
  if (extra) parts.push(`(${extra})`);
  return parts.join(' ');
}

/** Hostname if nmap resolved one, else the IP (else the MAC). */
export function deviceTitle(d: ProtocolDevice): string {
  const name = (d.hostnames ?? []).map(str).find((n): n is string => !!n);
  return name ?? str(d.ip) ?? str(d.mac) ?? str(d.ip6) ?? '';
}

/** Labelled fields in display order, only those present. */
export function deviceFields(d: ProtocolDevice): [string, string][] {
  const out: [string, string][] = [];
  const add = (label: string, v: unknown) => {
    const t = str(v);
    if (t) out.push([label, t]);
  };
  add('IP', d.ip);
  add('IP6', d.ip6);
  add('MAC', d.mac);
  add('VENDOR', d.vendor);
  for (const n of d.hostnames ?? []) add('HOST', n);
  const os = str(d.os);
  if (os) {
    const acc = pct(d.osAccuracy);
    out.push(['OS', acc ? `${os} (${acc})` : os]);
  }
  for (const alt of d.osAlternatives ?? []) {
    const name = str(alt?.name);
    if (!name) continue;
    const acc = pct(alt.accuracy);
    out.push(['ALT OS', acc ? `${name} (${acc})` : name]);
  }
  const type = str(d.type);
  if (type) out.push(['TYPE', type.toLowerCase()]);
  for (const p of d.ports ?? []) {
    const line = p ? portLine(p) : null;
    if (line) out.push(['PORT', line]);
  }
  const lat = num(d.latencyMs);
  if (lat !== null) out.push(['LATENCY', `${lat < 10 ? lat.toFixed(2) : lat.toFixed(1)} ms`]);
  const up = num(d.uptimeSeconds);
  if (up !== null) out.push(['UPTIME', humanUptime(up)]);
  add('BOOT', d.lastBoot);
  const hops = num(d.hops);
  if (hops !== null) out.push(['HOPS', String(Math.round(hops))]);
  return out;
}

/** Word-wrap `text` to `w` cells, hard-breaking words longer than `w`. */
export function wrap(text: string, w: number): string[] {
  const width = Math.max(1, Math.floor(w));
  const lines: string[] = [];
  let cur = '';
  for (const word of text.split(/\s+/).filter(Boolean)) {
    let rest = word;
    while (rest.length > width) {
      if (cur) {
        lines.push(cur);
        cur = '';
      }
      lines.push(rest.slice(0, width));
      rest = rest.slice(width);
    }
    if (!rest) continue;
    if (!cur) cur = rest;
    else if (cur.length + 1 + rest.length <= width) cur += ' ' + rest;
    else {
      lines.push(cur);
      cur = rest;
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

/** One device as a rounded ASCII box `width` cells wide:
 *  `╭─ title ──── [THIS HOST] ─╮` / `│ LABEL   value │` / `╰──╯`. */
export function deviceBox(d: ProtocolDevice, width: number): DeviceBox {
  const W = Math.max(MIN_BOX, Math.floor(width));
  const inner = W - 4; // "│ " + " │"
  const valueW = inner - LABEL_W;
  const host = d.isHost === true;
  const tag = host ? ` ${HOST_TAG} ` : '';
  const titleRoom = W - 2 - 3 - tag.length - 1; // ╭─ … ─╮ around the tag
  const title = deviceTitle(d);
  const t = title ? ` ${cut(title, Math.max(1, titleRoom))} ` : '';
  const top = '╭─' + t + '─'.repeat(Math.max(0, W - 4 - t.length - tag.length)) + tag + '─╮';
  const rows: BoxRow[] = [];
  for (const [label, value] of deviceFields(d)) {
    wrap(value, valueW).forEach((part, i) =>
      rows.push({ label: i === 0 ? label : '', value: part })
    );
  }
  const lines = [
    top,
    ...rows.map((r) => '│ ' + pad(r.label, LABEL_W) + pad(r.value, valueW) + ' │'),
    '╰' + '─'.repeat(W - 2) + '╯',
  ];
  return { title, host, ip: str(d.ip) ?? undefined, width: W, lines, rows };
}

function ipKey(ip: string | undefined): number {
  const parts = (ip ?? '').split('.').map(Number);
  if (parts.length !== 4 || parts.some((p) => !Number.isInteger(p) || p < 0 || p > 255))
    return Number.MAX_SAFE_INTEGER;
  return ((parts[0] * 256 + parts[1]) * 256 + parts[2]) * 256 + parts[3];
}

/** This host first, then ascending IPv4 order (stable otherwise). */
export function sortDevices(devices: ProtocolDevice[]): ProtocolDevice[] {
  return devices
    .map((d, i) => ({ d, i }))
    .sort(
      (a, b) =>
        Number(b.d.isHost === true) - Number(a.d.isHost === true) ||
        ipKey(a.d.ip) - ipKey(b.d.ip) ||
        a.i - b.i
    )
    .map((x) => x.d);
}

/** `DEVICES: N`, N = exactly the number of devices the scan returned. */
export function deviceCountLine(devices: ProtocolDevice[] | undefined): string {
  return `DEVICES: ${(devices ?? []).length}`;
}

/** Box width for a terminal `cols` cells wide: as many ~42-cell
 *  columns as fit, then widened to share the slack. */
export function boxWidthFor(cols: number, gap = 2): number {
  const c = Math.max(1, Math.floor(cols));
  if (c <= MIN_BOX) return MIN_BOX;
  const n = Math.max(1, Math.floor((c + gap) / (TARGET_BOX + gap)));
  const w = Math.floor((c - (n - 1) * gap) / n);
  return Math.max(MIN_BOX, Math.min(MAX_BOX, w, c));
}

export type GridCell = { x: number; y: number; w: number; h: number };
export type Grid = { cells: GridCell[]; columns: number; width: number; height: number };

/** Masonry pack: each box (same width) drops into the shortest column.
 *  Coordinates and sizes are in character cells. */
export function layoutGrid(
  boxes: { width: number; lines: string[] }[],
  cols: number,
  { gapX = 2, gapY = 1 }: { gapX?: number; gapY?: number } = {}
): Grid {
  const bw = boxes.length ? Math.max(...boxes.map((b) => b.width)) : 0;
  const columns = bw
    ? Math.max(1, Math.min(boxes.length, Math.floor((Math.max(1, cols) + gapX) / (bw + gapX))))
    : 0;
  const heights = Array<number>(columns).fill(0);
  const cells = boxes.map((b) => {
    let k = 0;
    for (let i = 1; i < columns; i++) if (heights[i] < heights[k]) k = i;
    const cell = { x: k * (bw + gapX), y: heights[k], w: b.width, h: b.lines.length };
    heights[k] += b.lines.length + gapY;
    return cell;
  });
  const height = columns ? Math.max(0, Math.max(...heights) - gapY) : 0;
  return { cells, columns, width: columns ? columns * bw + (columns - 1) * gapX : 0, height };
}
