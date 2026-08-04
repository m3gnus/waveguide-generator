export type RenderTask = () => void;

/**
 * R3F renders this many frames per invalidation.
 *
 * One is not enough. Verified in a real browser: after the scene and camera are
 * fully correct the canvas still showed a stale image, and a single extra
 * invalidate from the console painted it correctly every time. Mutations that
 * land once R3F has already processed the current frame need the following one
 * to reach the screen, so every invalidation covers two.
 */
const FRAMES_PER_INVALIDATION = 2;

export class DemandRenderScheduler {
  private readonly tasks = new Set<RenderTask>();

  constructor(private readonly invalidate: (frames?: number) => void) {}

  schedule(task?: RenderTask): () => void {
    if (task) this.tasks.add(task);
    this.invalidate(FRAMES_PER_INVALIDATION);
    return () => {
      if (task) this.tasks.delete(task);
    };
  }

  // Called from a priority-0 R3F useFrame callback. R3F renders only after all
  // priority-0 subscribers have run, so mutations made here reach this frame's
  // paint instead of requiring a second animation frame.
  flush(): void {
    const pending = [...this.tasks];
    this.tasks.clear();
    pending.forEach((task) => task());
  }

  dispose(): void {
    this.tasks.clear();
  }

  get pending(): boolean {
    return this.tasks.size > 0;
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
  window.__wg2ViewportTestHook = { forceFrame: () => scheduler.schedule() };
  return () => {
    if (previous) window.__wg2ViewportTestHook = previous;
    else delete window.__wg2ViewportTestHook;
  };
}
