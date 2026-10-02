/** Covered HUD surfaces can outlive either modal. Reference counts prevent a
 * suit/Insights handoff from restoring interaction beneath the next dialog. */
const covered = new Map<HTMLElement, { count: number; inert: boolean }>();
let overlays = 0;

export function coverHud(): () => void {
  const root = document.documentElement;
  const surfaces = Array.from(document.querySelectorAll<HTMLElement>('.hud, .sbar-root'));
  for (const surface of surfaces) {
    const entry = covered.get(surface) ?? { count: 0, inert: surface.inert };
    entry.count += 1;
    covered.set(surface, entry);
    surface.inert = true;
  }
  overlays += 1;
  // Existing ambient renderers observe this legacy class to suspend work.
  root.classList.add('suit-open');
  let released = false;
  return () => {
    if (released) return;
    released = true;
    for (const surface of surfaces) {
      const entry = covered.get(surface);
      if (!entry || --entry.count > 0) continue;
      surface.inert = entry.inert;
      covered.delete(surface);
    }
    if (--overlays === 0) root.classList.remove('suit-open');
  };
}
