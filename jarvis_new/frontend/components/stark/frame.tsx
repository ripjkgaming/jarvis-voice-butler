'use client';

import { type ReactNode } from 'react';
import { AsciiBox } from '@/components/ascii/glyphs';
import s from './stark.module.css';

/** LOCKE OS instrument panel, box-drawn: `┌─[ LABEL ]──── ASIDE ─ 01 ─┐`
 *  with a decoding title and an ASCII wipe-in (components/ascii). */
export function Frame({
  label,
  index,
  aside,
  className,
  bodyClassName,
  live,
  children,
}: {
  label: string;
  index: number;
  aside?: ReactNode;
  className?: string;
  bodyClassName?: string;
  live?: boolean;
  children: ReactNode;
}) {
  return (
    <AsciiBox
      label={label}
      index={index}
      aside={aside}
      live={live}
      className={`${s.frame} ${className ?? ''}`}
      bodyClassName={`${s.frameBody} ${bodyClassName ?? ''}`}
    >
      {children}
    </AsciiBox>
  );
}
