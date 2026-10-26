/** Preserve the existing two-click HUD gesture, backed by one-use server tickets. */
type Reply = { confirmation_required?: boolean; confirmation?: string; expires_in_ms?: number };
type Post = (body: Record<string, unknown>) => Promise<Reply | null>;

export class PowerConfirmation {
  private ticket: { action: string; token: string; deadline: number } | null = null;
  private busy = false;
  private generation = 0;
  private disposed = false;

  constructor(
    private readonly post: Post,
    private readonly changed: (action: string | null, busy: boolean) => void,
    private readonly now: () => number = () => performance.now()
  ) {}

  clear() {
    this.ticket = null;
    this.generation += 1;
    this.changed(null, this.busy);
  }

  dispose() {
    this.disposed = true;
    this.ticket = null;
    this.generation += 1;
  }

  async click(action: string, confirm: boolean, trusted: boolean): Promise<boolean> {
    if (!trusted || this.busy || this.disposed) return false;
    const prior = this.ticket;
    const use = confirm && prior?.action === action && this.now() < prior.deadline;
    this.ticket = null; // Clear before dispatch; never replay an uncertain request.
    this.busy = true;
    const generation = ++this.generation;
    this.changed(null, true);
    try {
      const body = !confirm
        ? { action }
        : use
          ? { action, phase: 'confirm', confirmation: prior.token }
          : { action, phase: 'request' };
      const reply = await this.post(body);
      if (this.disposed || generation !== this.generation) return false;
      if (confirm && !use) {
        if (
          reply?.confirmation_required === true &&
          typeof reply.confirmation === 'string' &&
          reply.confirmation.length > 0
        ) {
          this.ticket = { action, token: reply.confirmation, deadline: this.now() + 3500 };
        }
        return false;
      }
      return true; // Request finished; this does not claim a verified effect.
    } catch {
      return false;
    } finally {
      this.busy = false;
      if (!this.disposed) this.changed(this.ticket?.action ?? null, false);
    }
  }
}
