/** Coordinates rounded to 0.01: the static export and the webview disagree
 *  in the last float digit, which breaks hydration. */
export const r2 = (v: number) => Math.round(v * 100) / 100;

export function pt(r: number, deg: number): [number, number] {
  const a = (deg * Math.PI) / 180;
  return [r2(r * Math.cos(a)), r2(r * Math.sin(a))];
}

/** SVG arc from a0 to a1 degrees (clockwise, 0 = east). */
export function arc(r: number, a0: number, a1: number): string {
  const [x0, y0] = pt(r, a0);
  const [x1, y1] = pt(r, a1);
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
  return `M${x0} ${y0} A${r} ${r} 0 ${large} 1 ${x1} ${y1}`;
}
