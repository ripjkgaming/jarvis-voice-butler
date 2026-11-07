/** The four glyphs Commit Mono lacks (⌗ ⌬ ↵ ⚡), drawn as tiny SVGs so they
 * never fall back to an emoji font. All are decorative (aria-hidden). */
export const Hash = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M9 3 7 21M17 3l-2 18M4 8.5h17M3 15.5h17" />
  </svg>
);
/** ⌬ as a fingerprint-in-hexagon mark: the "needs fingerprint" sign. */
export const Print = ({ cls = '' }: { cls?: string }) => (
  <svg viewBox="0 0 24 24" aria-hidden="true" class={cls}>
    <path d="M12 2.5 20.2 7.2v9.6L12 21.5l-8.2-4.7V7.2Z" />
    <path d="M8.6 14.6c0-2.6.8-5 3.4-5s3.4 2.4 3.4 5M10.6 16.2c0-2.4.2-4.3 1.4-4.3s1.4 1.9 1.4 4.3" />
  </svg>
);
export const Enter = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M19 5v7a3 3 0 0 1-3 3H6M10 11l-4 4 4 4" />
  </svg>
);
export const Bolt = () => (
  <svg viewBox="0 0 7 10" aria-hidden="true" class="bolt">
    <path d="M4.2 0 0 5.6h2.6L2 10l5-6.2H4.3Z" />
  </svg>
);
