/** Local, fictional telemetry for the armor visualization. Never real hardware. */
export type SuitRegion =
  | 'head'
  | 'chest'
  | 'leftArm'
  | 'rightArm'
  | 'leftLeg'
  | 'rightLeg'
  | 'reactor';
export type SuitSnapshot = {
  damage: Record<SuitRegion, number>;
  reactor: number;
  fuel: number;
  coolant: number;
};
export const SUIT_REGIONS: { id: SuitRegion; label: string }[] = [
  { id: 'head', label: 'Helmet / optics' },
  { id: 'chest', label: 'Thoracic armor' },
  { id: 'reactor', label: 'Arc reactor' },
  { id: 'leftArm', label: 'Left arm / repulsor' },
  { id: 'rightArm', label: 'Right arm / repulsor' },
  { id: 'leftLeg', label: 'Left leg / thruster' },
  { id: 'rightLeg', label: 'Right leg / thruster' },
];
export const SUIT_PRESETS: Record<
  'nominal' | 'postFlight' | 'critical',
  { label: string; snapshot: SuitSnapshot }
> = {
  nominal: {
    label: 'Nominal',
    snapshot: {
      damage: { head: 0, chest: 2, reactor: 0, leftArm: 1, rightArm: 0, leftLeg: 2, rightLeg: 1 },
      reactor: 98,
      fuel: 94,
      coolant: 96,
    },
  },
  postFlight: {
    label: 'Post-flight',
    snapshot: {
      damage: {
        head: 7,
        chest: 16,
        reactor: 4,
        leftArm: 38,
        rightArm: 12,
        leftLeg: 9,
        rightLeg: 24,
      },
      reactor: 76,
      fuel: 58,
      coolant: 83,
    },
  },
  critical: {
    label: 'Critical',
    snapshot: {
      damage: {
        head: 23,
        chest: 62,
        reactor: 41,
        leftArm: 78,
        rightArm: 34,
        leftLeg: 19,
        rightLeg: 67,
      },
      reactor: 29,
      fuel: 18,
      coolant: 42,
    },
  },
};

/** Commands arrive on the already shared /sys snapshot; no suit polling loop. */
export type SuitCommand = {
  op: 'open' | 'close';
  revision: number;
  issued_at: number;
  session: string;
};
export type SuitCommandCursor = { session: string; revision: number };
export function validSuitCommand(value: unknown): value is SuitCommand {
  if (!value || typeof value !== 'object') return false;
  const command = value as Partial<SuitCommand>;
  return (
    (command.op === 'open' || command.op === 'close') &&
    typeof command.session === 'string' &&
    command.session.length > 0 &&
    Number.isSafeInteger(command.revision) &&
    (command.revision ?? -1) >= 0 &&
    typeof command.issued_at === 'number' &&
    Number.isFinite(command.issued_at) &&
    command.issued_at >= 0
  );
}
export function consumeSuitCommand(
  command: unknown,
  cursor: SuitCommandCursor | null,
  mountedAt: number
): {
  cursor: SuitCommandCursor | null;
  open: boolean | null;
} {
  if (!validSuitCommand(command)) return { cursor, open: null };
  if (cursor?.session === command.session && command.revision <= cursor.revision)
    return { cursor, open: null };
  const next = { session: command.session, revision: command.revision };
  // Never replay an old open after mounting/reloading. A bridge restart closes
  // the panel even when its clock differs; explicit fresh requests can reopen it.
  return {
    cursor: next,
    open: command.op === 'close' ? false : command.issued_at >= mountedAt ? true : null,
  };
}
