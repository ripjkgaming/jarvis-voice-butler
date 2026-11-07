/** Control actions (PLAN.md §6.4, §4.5): pending → verified/warn/failed,
 * fingerprint step-up for guest_off/unlock/restart/shutdown, haptics + toast
 * on failure. POSTs are never retried. */
import { api, ApiError } from './api';
import { buzz, HAPTIC } from './motion/haptics';
import { actions, canControl, showToast, STEPUP, type ControlAction } from './store';
import { assertion } from './webauthn';

type ControlReply = { ok: boolean; status: string; error?: string; state?: Record<string, unknown> };
export type ControlHooks = { applyState: (state: Record<string, unknown> | undefined) => void; refresh: () => void };

const LABEL: Record<ControlAction, string> = {
  guest_on: 'GUEST ON',
  guest_off: 'GUEST OFF',
  lock: 'LOCK',
  unlock: 'UNLOCK',
  screens_off: 'SCREENS OFF',
  screens_on: 'SCREENS ON',
  sleep: 'SLEEP',
  restart: 'RESTART',
  shutdown: 'SHUTDOWN',
  mic_mute: 'MIC MUTE',
  mic_unmute: 'MIC UNMUTE',
};

function mark(action: ControlAction, phase: 'pending' | 'ok' | 'warn' | 'failed', error?: string) {
  actions.value = { ...actions.value, [action]: { phase, error, at: Date.now() } };
}

export async function runAction(action: ControlAction, hooks: ControlHooks): Promise<'ok' | 'warn' | 'failed'> {
  if (!canControl.value) return 'failed';
  if (actions.value[action]?.phase === 'pending') return 'failed';
  mark(action, 'pending');
  let proof: Record<string, unknown> | undefined;
  try {
    if (STEPUP.has(action)) proof = await assertion(action);
    const res = await api<ControlReply>('POST', '/phone/v1/control', { action, ...(proof ? { assertion: proof } : {}) });
    hooks.applyState(res.state);
    hooks.refresh();
    if (!res.ok || res.status === 'failed' || res.status === 'mismatched') {
      const why = res.status === 'mismatched' ? 'READBACK MISMATCH' : 'FAILED';
      mark(action, 'failed', why);
      buzz(HAPTIC.failed);
      showToast(`${LABEL[action]} ${why}`, 'fail');
      return 'failed';
    }
    const warn = res.status === 'dispatched_unverified';
    mark(action, warn ? 'warn' : 'ok');
    buzz(warn ? HAPTIC.tap : HAPTIC.verified);
    if (warn) showToast(`${LABEL[action]} SENT · NOT VERIFIABLE`);
    return warn ? 'warn' : 'ok';
  } catch (e) {
    const err = e as ApiError | Error;
    const why =
      err instanceof ApiError
        ? err.kind === 'offline'
          ? 'NO LINK'
          : err.kind === 'stepup'
            ? 'FINGERPRINT REJECTED'
            : err.kind === 'gate' || err.kind === 'auth'
              ? 'GATE CLOSED'
              : 'FAILED'
        : err.name === 'NotAllowedError' || /cancel/i.test(err.message)
          ? 'FINGERPRINT CANCELLED'
          : 'FAILED';
    mark(action, 'failed', why);
    buzz(HAPTIC.failed);
    showToast(`${LABEL[action]} · ${why}`, 'fail');
    return 'failed';
  }
}
