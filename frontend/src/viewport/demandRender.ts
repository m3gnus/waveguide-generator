export type RenderTask = () => void;
export type RequestFrame = (callback: FrameRequestCallback) => number;
export type CancelFrame = (handle: number) => void;

export class DemandRenderScheduler {
  private handle: number | null = null;
  private readonly tasks = new Set<RenderTask>();

  constructor(
    private readonly render: () => void,
    // Arrow wrappers keep the window binding: a bare requestAnimationFrame
    // reference throws "Illegal invocation" when called as this.requestFrame()
    // in real browsers (jsdom doesn't enforce the binding, so tests can't see it).
    private readonly requestFrame: RequestFrame = (cb) => window.requestAnimationFrame(cb),
    private readonly cancelFrame: CancelFrame = (handle) => window.cancelAnimationFrame(handle),
  ) {}

  schedule(task?: RenderTask): () => void {
    if (task) this.tasks.add(task);
    if (this.handle === null) this.handle = this.requestFrame(() => this.flush());
    return () => {
      if (task) this.tasks.delete(task);
    };
  }

  flush(): void {
    if (this.handle !== null) this.cancelFrame(this.handle);
    this.handle = null;
    const pending = [...this.tasks];
    this.tasks.clear();
    pending.forEach((task) => task());
    this.render();
  }

  dispose(): void {
    if (this.handle !== null) this.cancelFrame(this.handle);
    this.handle = null;
    this.tasks.clear();
  }

  get pending(): boolean {
    return this.handle !== null;
  }
}

declare global {
  interface Window {
    __wg2ViewportTestHook?: { forceFrame(): void };
  }
}

export function installViewportTestHook(scheduler: DemandRenderScheduler): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const previous = window.__wg2ViewportTestHook;
  window.__wg2ViewportTestHook = { forceFrame: () => scheduler.flush() };
  return () => {
    if (previous) window.__wg2ViewportTestHook = previous;
    else delete window.__wg2ViewportTestHook;
  };
}
