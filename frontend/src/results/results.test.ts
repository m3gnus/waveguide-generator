import { describe, expect, it, vi } from 'vitest';
import { CompareStore, fetchJobResults, ResultsLruCache, resultsCache, type JobResults } from '../api/results';
import { complexToDb, directivityGrid, impedanceSeries, polarSeries, splSeries } from './mappers';

function result(offset = 0): JobResults {
  return {
    frequencies: [200, 1_000],
    spl_on_axis: { frequencies: [200, 1_000], spl: [90 + offset, 96 + offset], phase_degrees: [0, 20] },
    directivity: {
      horizontal: [
        [[-90, -18], [0, 0], [90, -18]],
        [[-90, [0.1, 0]], [0, [1, 0]], [90, [0.1, 0]]],
      ],
      vertical: [
        [[-90, -24], [0, 0], [90, -24]],
        [[-90, -30], [0, 0], [90, -30]],
      ],
    },
    impedance: { frequencies: [200, 1_000], real: [1, 0], imaginary: [0, 1] },
  };
}

describe('results LRU', () => {
  it('caps at 15 and evicts the least recently used job', () => {
    const cache = new ResultsLruCache(15);
    for (let index = 0; index < 15; index += 1) cache.set(`job-${index}`, result(index));
    expect(cache.get('job-0')).toBeDefined();
    cache.set('job-15', result(15));
    expect(cache.has('job-0')).toBe(true);
    expect(cache.has('job-1')).toBe(false);
    expect(cache.keys()).toHaveLength(15);
  });

  it('clamps oversized cache budgets to 15', () => {
    expect(new ResultsLruCache(100).maxEntries).toBe(15);
    expect(new ResultsLruCache(Number.NaN).maxEntries).toBe(15);
  });

  it('shares one request when multiple panels ask for the same job concurrently', async () => {
    resultsCache.clear();
    const fetcher = vi.fn(async () => new Response(JSON.stringify(result()), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    const [first, second] = await Promise.all([
      fetchJobResults('shared-job', fetcher),
      fetchJobResults('shared-job', fetcher),
    ]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);
  });

  it('prunes deleted primary and overlay job ids', () => {
    const selection = new CompareStore();
    selection.setPrimary('primary');
    selection.toggleOverlay('keep');
    selection.toggleOverlay('deleted');
    selection.prune(new Set(['keep']));
    expect(selection.getSnapshot()).toEqual({ primary: null, overlays: ['keep'], following: true });
  });

  it('follows the newest solve until a result is chosen by hand', () => {
    const selection = new CompareStore();
    expect(selection.getSnapshot().following).toBe(true);
    selection.followLatest('solve-1');
    expect(selection.getSnapshot()).toMatchObject({ primary: 'solve-1', following: true });
    selection.followLatest('solve-2');
    expect(selection.getSnapshot().primary).toBe('solve-2');

    selection.setPrimary('older');
    expect(selection.getSnapshot()).toMatchObject({ primary: 'older', following: false });
    selection.followLatest('solve-3');
    expect(selection.getSnapshot()).toMatchObject({ primary: 'solve-3', following: true });
  });

  it('resumes following once a pinned result is dropped', () => {
    const selection = new CompareStore();
    selection.setPrimary('pinned');
    expect(selection.getSnapshot().following).toBe(false);
    selection.remove('pinned');
    expect(selection.getSnapshot()).toMatchObject({ primary: null, following: true });
  });

  it('keeps a pinned result pinned when unrelated jobs are pruned', () => {
    const selection = new CompareStore();
    selection.setPrimary('pinned');
    selection.toggleOverlay('gone');
    selection.prune(new Set(['pinned']));
    expect(selection.getSnapshot()).toEqual({ primary: 'pinned', overlays: [], following: false });
  });

  it('holds one snapshot identity while the selection is unchanged', () => {
    // Every jobs message prunes this store, so during a solve it is written to
    // several times a second. A new identity for an unchanged selection
    // rebuilt every chart's ECharts option, which read on screen as a blink.
    const selection = new CompareStore();
    selection.followLatest('solve-1');
    selection.toggleOverlay('overlay-1');
    const before = selection.getSnapshot();
    let notifications = 0;
    const stop = selection.subscribe(() => { notifications += 1; });

    selection.followLatest('solve-1');
    selection.prune(new Set(['solve-1', 'overlay-1']));
    selection.prune(new Set(['solve-1', 'overlay-1']));
    expect(selection.getSnapshot()).toBe(before);
    expect(notifications).toBe(0);

    selection.followLatest('solve-2');
    expect(selection.getSnapshot()).not.toBe(before);
    expect(notifications).toBe(1);
    stop();
  });

  it('notifies when only the follow state changes', () => {
    const selection = new CompareStore();
    selection.followLatest('solve-1');
    let notifications = 0;
    const stop = selection.subscribe(() => { notifications += 1; });
    // Same primary, same overlays: only the pin changed, and the toolbar has
    // to see it.
    selection.setPrimary('solve-1');
    expect(selection.getSnapshot()).toMatchObject({ primary: 'solve-1', following: false });
    expect(notifications).toBe(1);
    stop();
  });
});

describe('chart data mappers', () => {
  it('maps independent frequency arrays into named SPL overlay series', () => {
    const series = splSeries([{ id: 'a', label: 'Primary', result: result() }, { id: 'b', label: 'Overlay', result: result(3) }]);
    expect(series.map((item) => item.name)).toEqual(['Primary', 'Overlay']);
    expect(series[1].data).toEqual([[200, 93], [1_000, 99]]);
  });

  it('builds the angle/frequency heatmap grid and converts complex pressure to dB', () => {
    const grid = directivityGrid(result());
    expect(grid.angles).toEqual([-90, 0, 90]);
    expect(grid.data).toHaveLength(6);
    expect(grid.data.find(([f, angle]) => f === 1_000 && angle === -90)?.[2]).toBeCloseTo(-20);
    expect(complexToDb(0, 0)).toBeNull();
    expect(complexToDb(1, 0)).toBe(0);
  });

  it('maps frequency-selected H/V polars and both impedance modes', () => {
    expect(polarSeries(result(), 0, 'vertical')).toEqual([[-24, -90], [0, 0], [-24, 90]]);
    const cartesian = impedanceSeries(result(), 'cartesian');
    const magnitude = impedanceSeries(result(), 'polar');
    expect(cartesian[1].data[1]).toEqual([1_000, 1]);
    expect(magnitude[0].data).toEqual([[200, 1], [1_000, 1]]);
    expect(magnitude[1].data[1][1]).toBeCloseTo(90);
  });
});
