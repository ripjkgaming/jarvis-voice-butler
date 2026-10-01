import styles from './fallback-surface.module.css';

type FallbackSurfaceProps = {
  code: '404' | 'DISPLAY';
  title: string;
  description: string;
};

/** Passive status display: recovery stays with the existing voice commands. */
export function FallbackSurface({ code, title, description }: FallbackSurfaceProps) {
  return (
    <main className={styles.root} aria-labelledby="fallback-title">
      <header className={styles.header}>
        <span className={styles.brand}>J.A.R.V.I.S.</span>
        <span className={styles.system}>STARK OS / DISPLAY STATUS</span>
      </header>
      <section className={styles.panel}>
        <svg className={styles.reactor} viewBox="0 0 120 120" aria-hidden="true">
          <circle cx="60" cy="60" r="53" />
          <circle cx="60" cy="60" r="44" strokeDasharray="13 5" />
          <circle cx="60" cy="60" r="33" />
          <path d="M60 36 81 73H39Z" />
          <path d="M60 0v14M60 106v14M0 60h14M106 60h14" />
        </svg>
        <p className={styles.code}>◢ STATUS / {code}</p>
        <h1 id="fallback-title">{title}</h1>
        <p className={styles.description}>{description}</p>
        <div className={styles.guidance}>
          <h2>VOICE GUIDANCE</h2>
          <p>Ask Jarvis to open another view:</p>
          <ul>
            <li>“Open research projects”</li>
            <li>“Open my drafts”</li>
          </ul>
          <p className={styles.note}>Voice commands run independently of this display.</p>
        </div>
      </section>
      <footer className={styles.footer}>STARK INDUSTRIES // JARVIS DISPLAY SYSTEM</footer>
    </main>
  );
}
