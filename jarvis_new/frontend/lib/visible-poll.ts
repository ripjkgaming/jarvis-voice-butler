/** Serialize async reads, pause hidden windows, and cancel work on cleanup.
 * A resumed window refreshes immediately; slow reads never pile up. Callers
 * must check the signal before applying results (including fail-soft nulls).
 */
export function startVisiblePoll(
  read: (signal: AbortSignal) => Promise<void>,
  intervalMs: number
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let active: AbortController | null = null;

  const run = async () => {
    timer = null;
    if (stopped || document.hidden || active) return;
    const controller = new AbortController();
    active = controller;
    try {
      await read(controller.signal);
    } catch {
      // A transient reader failure must not permanently stop refreshes.
    } finally {
      active = null;
      if (!stopped && !document.hidden) {
        timer = setTimeout(() => void run(), controller.signal.aborted ? 0 : intervalMs);
      }
    }
  };
  const visibility = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (document.hidden) active?.abort();
    else if (!active) void run();
  };

  document.addEventListener('visibilitychange', visibility);
  void run();
  return () => {
    stopped = true;
    if (timer !== null) clearTimeout(timer);
    active?.abort();
    document.removeEventListener('visibilitychange', visibility);
  };
}
