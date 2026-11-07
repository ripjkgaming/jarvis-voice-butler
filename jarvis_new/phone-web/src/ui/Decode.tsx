export function Decode({ text, cls }: { text: string; cls?: string; ms?: number }) {
  return <span class={cls}>{text}</span>;
}
