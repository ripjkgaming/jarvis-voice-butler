/** Hold confirmation uses a timer and static feedback, with no animation. */
import { useEffect, useRef, useState } from 'preact/hooks';
export const HOLD_MS = 1200;
export function HoldButton({ label, aria, tap, disabled, onFire }: {
  label: string; aria: string; tap?: boolean; disabled?: boolean; onFire: () => void;
}) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [holding, setHolding] = useState(false);
  const cancel = () => { if (timer.current) clearTimeout(timer.current); timer.current = null; setHolding(false); };
  useEffect(() => { if (disabled) cancel(); return cancel; }, [disabled]);
  return <button type="button" class="hold" aria-label={aria} disabled={disabled}
    onPointerDown={(e) => {
      e.stopPropagation();
      if (disabled || tap) return;
      cancel();
      setHolding(true);
      e.currentTarget.setPointerCapture?.(e.pointerId);
      timer.current = setTimeout(() => { timer.current = null; setHolding(false); onFire(); }, HOLD_MS);
    }} onPointerUp={cancel} onPointerCancel={cancel} onLostPointerCapture={cancel}
    onClick={(e) => { e.stopPropagation(); if (!disabled && (tap || e.detail === 0)) onFire(); }}>
    {holding ? 'Keep holding…' : label}
  </button>;
}
