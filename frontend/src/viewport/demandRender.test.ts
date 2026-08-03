import { describe, expect, it, vi } from 'vitest';
import { DemandRenderScheduler, type RequestFrame } from './demandRender';

describe('DemandRenderScheduler', () => {
  it('batches uploads into one frame and performs no rendering while idle', () => {
    let callback: FrameRequestCallback | null = null;
    const request = vi.fn<RequestFrame>((next) => {
      callback = next;
      return 17;
    });
    const render = vi.fn();
    const first = vi.fn();
    const second = vi.fn();
    const scheduler = new DemandRenderScheduler(render, request, vi.fn());

    scheduler.schedule(first);
    scheduler.schedule(second);
    expect(request).toHaveBeenCalledOnce();
    expect(render).not.toHaveBeenCalled();
    expect(first).not.toHaveBeenCalled();
    expect(second).not.toHaveBeenCalled();

    const queued = callback as FrameRequestCallback | null;
    if (!queued) throw new Error('frame was not queued');
    queued(0);
    expect(first).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledOnce();
    expect(render).toHaveBeenCalledOnce();

    expect(request).toHaveBeenCalledOnce();
    expect(render).toHaveBeenCalledOnce();
  });

  it('supports an explicit at-rest frame for screenshot tests', () => {
    const render = vi.fn();
    const scheduler = new DemandRenderScheduler(render, vi.fn(() => 1), vi.fn());
    scheduler.flush();
    expect(render).toHaveBeenCalledOnce();
  });
});
