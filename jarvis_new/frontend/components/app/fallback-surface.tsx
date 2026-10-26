import { AsciiBox, SayHint } from '@/components/ascii/glyphs';
import { reticleArt } from '@/lib/ascii';
import styles from './fallback-surface.module.css';

type FallbackSurfaceProps = {
  code: '404' | 'DISPLAY';
  title: string;
  description: string;
};

/** Box-drawn reticle (static text art, computed once at module load). */
const RETICLE = reticleArt(29, 13, 1.7).join('\n');

/** Passive status display: recovery stays with the existing voice commands. */
export function FallbackSurface({ code, title, description }: FallbackSurfaceProps) {
  return (
    <main className={styles.root} aria-labelledby="fallback-title">
      <header className={styles.header}>
        <span className={styles.brand}>J.A.R.V.I.S.</span>
        <span className={styles.system}>LOCKE OS / DISPLAY STATUS</span>
      </header>
      <AsciiBox
        label={`STATUS / ${code}`}
        heavy
        tone="amber"
        className={styles.panel}
        bodyClassName={styles.panelBody}
      >
        <pre className={styles.reactor} aria-hidden="true">
          {RETICLE}
        </pre>
        <p className={styles.code}>▲ STATUS / {code}</p>
        <h1 id="fallback-title">{title}</h1>
        <p className={styles.description}>{description}</p>
        <div className={styles.guidance}>
          <h2>VOICE GUIDANCE</h2>
          <p>Ask Jarvis to open another view:</p>
          <ul>
            <li>
              <SayHint>Open research projects</SayHint>
            </li>
            <li>
              <SayHint>Open my drafts</SayHint>
            </li>
          </ul>
          <p className={styles.note}>Voice commands run independently of this display.</p>
        </div>
      </AsciiBox>
    </main>
  );
}
