/** navigator.vibrate patterns (PLAN.md §4.6). No-ops where unsupported; never
 * the only feedback for anything. */
export const HAPTIC = {
  tap: 8,
  verified: [12, 40, 12],
  failed: [40, 30, 40],
  hold: 5,
  connected: [10, 60, 20],
  dropped: [60],
  gate: [8, 30, 8, 30, 16],
} as const;

export function buzz(pattern: number | readonly number[]) {
  try {
    navigator.vibrate?.(pattern as number | number[]);
  } catch {
    /* unsupported */
  }
}
