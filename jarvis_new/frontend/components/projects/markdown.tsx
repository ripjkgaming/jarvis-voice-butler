'use client';

/** Tiny markdown renderer for research reports: headings, lists, fenced
 *  code, paragraphs, **bold**, `code`, [links](url) and bare URLs. Builds
 *  React nodes only (no innerHTML), so model output can't inject markup. */
import { Fragment, type ReactNode } from 'react';
import styles from './project-archive.module.css';

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^)\s]+)\)|https?:\/\/[^\s)]+)/g;

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const tok = m[0];
    const k = `${key}-${i++}`;
    if (tok.startsWith('**')) {
      out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith('`')) {
      out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith('[')) {
      const label = tok.slice(1, tok.indexOf(']'));
      out.push(
        <a key={k} href={m[2]} target="_blank" rel="noreferrer noopener">
          {label}
        </a>
      );
    } else {
      out.push(
        <a key={k} href={tok} target="_blank" rel="noreferrer noopener">
          {tok}
        </a>
      );
    }
    last = at + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let fence: string[] | null = null;

  const flushPara = () => {
    if (para.length) {
      const k = `p${blocks.length}`;
      blocks.push(<p key={k}>{inline(para.join(' '), k)}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const k = `l${blocks.length}`;
      const items = list.items.map((it, n) => <li key={n}>{inline(it, `${k}-${n}`)}</li>);
      blocks.push(list.ordered ? <ol key={k}>{items}</ol> : <ul key={k}>{items}</ul>);
      list = null;
    }
  };

  for (const raw of lines) {
    if (fence) {
      if (raw.trim().startsWith('```')) {
        blocks.push(<pre key={`f${blocks.length}`}>{fence.join('\n')}</pre>);
        fence = null;
      } else {
        fence.push(raw);
      }
      continue;
    }
    const line = raw.trimEnd();
    if (line.trim().startsWith('```')) {
      flushPara();
      flushList();
      fence = [];
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      flushList();
      const k = `h${blocks.length}`;
      const lvl = h[1].length;
      const Tag = (lvl <= 1 ? 'h2' : lvl === 2 ? 'h3' : 'h4') as 'h2' | 'h3' | 'h4';
      blocks.push(<Tag key={k}>{inline(h[2], k)}</Tag>);
      continue;
    }
    const li = /^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (li) {
      flushPara();
      const ordered = Boolean(li[2]);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(li[3]);
      continue;
    }
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    flushList();
    para.push(line.trim());
  }
  if (fence) blocks.push(<pre key={`f${blocks.length}`}>{fence.join('\n')}</pre>);
  flushPara();
  flushList();
  return (
    <div className={styles.md}>
      {blocks.map((b, i) => (
        <Fragment key={i}>{b}</Fragment>
      ))}
    </div>
  );
}
