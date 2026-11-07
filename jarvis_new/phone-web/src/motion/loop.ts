/** The single requestAnimationFrame loop (PLAN.md §4). Tasks run once per
 * frame and return false when they have nothing left to do; timers use the
 * loop clock (not setTimeout) so stagger delays stay frame-accurate. The loop
 * stops itself when no task or timer is left. */

export type Task = (dt: number, now: number) => boolean;
type Timer = { at: number; fn: () => void };

export class Loop {
  private tasks = new Set<Task>();
  private timers: Timer[] = [];
  private running = false;
  private last = 0;
  private clock: number;
  frames = 0;

  constructor(
    private raf: (cb: (t: number) => void) => unknown = (cb) => requestAnimationFrame(cb),
    private nowFn: () => number = () => performance.now(),
  ) {
    this.clock = nowFn();
  }

  /** Loop clock in ms: the timestamp of the frame being processed. */
  now() {
    return this.running ? this.clock : this.nowFn();
  }

  get active() {
    return this.running;
  }

  add(task: Task) {
    this.tasks.add(task);
    this.kick();
    return () => this.tasks.delete(task);
  }

  remove(task: Task) {
    this.tasks.delete(task);
  }

  has(task: Task) {
    return this.tasks.has(task);
  }

  /** Run fn once `ms` of loop time has passed. Returns a cancel function. */
  after(ms: number, fn: () => void) {
    const timer = { at: this.now() + ms, fn };
    this.timers.push(timer);
    this.kick();
    return () => {
      this.timers = this.timers.filter((t) => t !== timer);
    };
  }

  private kick() {
    if (this.running) return;
    this.running = true;
    this.last = this.nowFn();
    this.clock = this.last;
    this.raf(this.frame);
  }

  private frame = (t: number) => {
    const dt = Math.max(0, (t - this.last) / 1000);
    this.last = t;
    this.clock = t;
    this.frames++;
    if (this.timers.length) {
      const due = this.timers.filter((x) => x.at <= t);
      if (due.length) {
        this.timers = this.timers.filter((x) => x.at > t);
        for (const x of due) x.fn();
      }
    }
    for (const task of [...this.tasks]) {
      if (!task(dt, t)) this.tasks.delete(task);
    }
    if (this.tasks.size || this.timers.length) this.raf(this.frame);
    else this.running = false;
  };
}

export const loop = new Loop();
