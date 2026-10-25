import type { PieceType } from '@/lib/chess/types';

/** Original line-art pieces on a 45x45 grid. Colour comes from `currentColor`. */
const ART: Record<PieceType, React.ReactNode> = {
  p: (
    <>
      <circle cx="22.5" cy="13.5" r="5.5" />
      <path d="M16.5 35 Q17.5 26 22.5 21.5 Q27.5 26 28.5 35 Z" />
      <path d="M12 38.5 H33 M14.5 35 H30.5" />
    </>
  ),
  r: (
    <>
      <path d="M13 19 V9.5 H17.5 V14 H20.5 V9.5 H24.5 V14 H27.5 V9.5 H32 V19 Z" />
      <path d="M15.5 19 V35 M29.5 19 V35" />
      <path d="M12 38.5 H33 V35 H12 Z" />
      <path d="M13 19 H32" />
    </>
  ),
  b: (
    <>
      <circle cx="22.5" cy="6.5" r="1.8" />
      <path d="M22.5 9 C28 14 29 19.5 25.5 23 H19.5 C16 19.5 17 14 22.5 9 Z" />
      <path d="M19.5 15.5 H25.5 M22.5 12.5 V18.5" />
      <path d="M20 23 L18 33 H27 L25 23" />
      <path d="M13 38.5 H32 M15 35.5 H30" />
    </>
  ),
  n: (
    <>
      <path d="M14 37 C14 30 16.5 25.5 20.5 22 C16.5 22 13.5 24.5 11.5 28.5 L10 25.5 C12 19.5 16 13 22 10.5 L22 7 L25.5 9.5 C32.5 11 35.5 19 35.5 37 Z" />
      <circle cx="21.5" cy="15.5" r="1.1" />
      <path d="M12.5 38.5 H35.5" />
    </>
  ),
  q: (
    <>
      <path d="M11 36 L7.5 14 L15.5 24.5 L19 10.5 L22.5 24 L26 10.5 L29.5 24.5 L37.5 14 L34 36 Z" />
      <circle cx="7.5" cy="12" r="1.8" />
      <circle cx="19" cy="8.5" r="1.8" />
      <circle cx="26" cy="8.5" r="1.8" />
      <circle cx="37.5" cy="12" r="1.8" />
      <path d="M10.5 38.5 H34.5 M12 35 H33" />
    </>
  ),
  k: (
    <>
      <path d="M22.5 4.5 V12.5 M18.5 8 H26.5" />
      <path d="M22.5 13 C14.5 13 11 20.5 14 28 L12 36 H33 L31 28 C34 20.5 30.5 13 22.5 13 Z" />
      <path d="M17 24 H28 M12 38.5 H33" />
    </>
  ),
};

export function PieceArt({
  type,
  className,
  title,
}: {
  type: PieceType;
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 45 45"
      className={className}
      fill="currentColor"
      fillOpacity={0.16}
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      {ART[type]}
    </svg>
  );
}
