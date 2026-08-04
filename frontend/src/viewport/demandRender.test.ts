import { describe, expect, it, vi } from 'vitest';
import { DemandRenderScheduler, installViewportTestHook } from './demandRender';

class FakeFrameDriver {
  private invalidated = false;
  private frameCallback: (() => void) | null = null;

  readonly invalidate = vi.fn(() => {
    this.invalidated = true;
  });

  useFrame(callback: () => void): void {
    this.frameCallback = callback;
  }

  advance(render: () => void): void {
    if (!this.invalidated || !this.frameCallback) return;
    this.invalidated = false;
    this.frameCallback();
    render();
  }
}

describe('DemandRenderScheduler', () => {
  it('executes scheduled work inside the R3F frame callback before that frame renders', () => {
    const driver = new FakeFrameDriver();
    const scheduler = new DemandRenderScheduler(driver.invalidate);
    let phase: 'idle' | 'frame' | 'render' = 'idle';
    const order: string[] = [];
    driver.useFrame(() => {
      phase = 'frame';
      order.push('frame');
      scheduler.flush();
    });

    scheduler.schedule(() => {
      expect(phase).toBe('frame');
      order.push('task');
    });
    expect(order).toEqual([]);

    driver.advance(() => {
      phase = 'render';
      order.push('render');
    });

    expect(order).toEqual(['frame', 'task', 'render']);
  });

  it('coalesces queued tasks into the next invalidated R3F frame and stays idle afterward', () => {
    const driver = new FakeFrameDriver();
    const scheduler = new DemandRenderScheduler(driver.invalidate);
    const first = vi.fn();
    const second = vi.fn();
    const render = vi.fn();
    driver.useFrame(() => scheduler.flush());

    scheduler.schedule(first);
    scheduler.schedule(second);
    expect(first).not.toHaveBeenCalled();
    expect(second).not.toHaveBeenCalled();

    driver.advance(render);
    expect(first).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledOnce();
    expect(render).toHaveBeenCalledOnce();
    expect(scheduler.pending).toBe(false);

    driver.advance(render);
    expect(render).toHaveBeenCalledOnce();
  });

  it('routes the at-rest browser hook through R3F invalidation', () => {
    const invalidate = vi.fn();
    const scheduler = new DemandRenderScheduler(invalidate);
    const uninstall = installViewportTestHook(scheduler);

    window.__wg2ViewportTestHook?.forceFrame();

    expect(invalidate).toHaveBeenCalledOnce();
    uninstall();
  });
});
