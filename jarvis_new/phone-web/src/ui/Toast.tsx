import { useEffect } from 'preact/hooks';
import { toast } from '../store';

export function Toast() {
  const t = toast.value;
  useEffect(() => {
    if (!t) return;
    const id = setTimeout(() => {
      if (toast.value === t) toast.value = null;
    }, 4000);
    return () => clearTimeout(id);
  }, [t]);
  return (
    <div class={`toast${t?.tone === 'fail' ? ' fail' : ''}`} role="status" hidden={!t}>
      {t?.text ?? ''}
    </div>
  );
}
