/** fetch wrapper for the bridge behind the /api mount (PLAN.md §3).
 * Same-origin cookies, X-Jarvis-Phone: 1, 8 s timeout, typed errors.
 * POSTs are never retried or queued. Auth errors update the auth slice. */
import { auth } from './store';

export type ApiErrorKind = 'offline' | 'auth' | 'gate' | 'stepup' | 'server';

export class ApiError extends Error {
  constructor(
    public kind: ApiErrorKind,
    public status: number,
    public body: Record<string, unknown> = {},
  ) {
    super(typeof body.error === 'string' ? body.error : kind);
  }
}

export const TIMEOUT_MS = 8000;

export function classify(status: number, body: Record<string, unknown>): ApiErrorKind {
  if (status === 401 && body.auth === 'pair') return 'auth';
  if (status === 401 && body.error === 'gate') return 'gate';
  if (status === 403 && body.error === 'stepup') return 'stepup';
  return 'server';
}

type Opts = { timeoutMs?: number; fetchFn?: typeof fetch };

export async function api<T = Record<string, unknown>>(
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  opts: Opts = {},
): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? TIMEOUT_MS);
  let res: Response;
  try {
    res = await (opts.fetchFn ?? fetch)('/api' + path, {
      method,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'X-Jarvis-Phone': '1', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
  } catch {
    throw new ApiError('offline', 0);
  } finally {
    clearTimeout(timer);
  }
  let data: Record<string, unknown> = {};
  try {
    data = (await res.json()) as Record<string, unknown>;
  } catch {
    data = {};
  }
  if (!res.ok) {
    const kind = classify(res.status, data);
    if (kind === 'auth') auth.value = { ...auth.value, paired: false, gate: 'cold' };
    if (kind === 'gate') {
      const g = data.gate === 'soft' ? 'soft' : 'cold';
      auth.value = { ...auth.value, paired: true, gate: g, softAt: g === 'soft' ? Date.now() : null };
    }
    throw new ApiError(kind, res.status, data);
  }
  return data as T;
}
