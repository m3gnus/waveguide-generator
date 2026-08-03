import { describe, expect, it } from 'vitest';
import { CompareStore, ResultsLruCache, type JobResults } from '../api/results';
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

  it('prunes deleted primary and overlay job ids', () => {
    const selection = new CompareStore();
    selection.setPrimary('primary');
    selection.toggleOverlay('keep');
    selection.toggleOverlay('deleted');
    selection.prune(new Set(['keep']));
    expect(selection.getSnapshot()).toEqual({ primary: null, overlays: ['keep'] });
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
