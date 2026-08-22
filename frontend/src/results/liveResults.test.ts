import { describe, expect, it, vi } from 'vitest';
import { mergeProvisionalResults, ProvisionalResultsStore, type ResultData } from '../api/results';

describe('progressive live result presentation', () => {
  it('keeps frequency-shaped chart rows sorted during progressive solves', () => {
    const low: ResultData = {
      frequencies: [100],
      spl_on_axis: { frequencies: [100], spl: [1] },
      directivity: { horizontal: [[[0, -1]]] },
    };
    const high: ResultData = {
      frequencies: [700],
      spl_on_axis: { frequencies: [700], spl: [7] },
      directivity: { horizontal: [[[0, -7]]] },
    };
    const middle: ResultData = {
      frequencies: [400],
      spl_on_axis: { frequencies: [400], spl: [4] },
      directivity: { horizontal: [[[0, -4]]] },
    };

    const merged = mergeProvisionalResults(mergeProvisionalResults(low, high), middle);

    expect(merged.frequencies).toEqual([100, 400, 700]);
    expect(merged.spl_on_axis).toEqual({ frequencies: [100, 400, 700], spl: [1, 4, 7] });
    expect(merged.directivity?.horizontal).toEqual([[[0, -1]], [[0, -4]], [[0, -7]]]);
  });

  it('paints the first row immediately and coalesces later chart refreshes', () => {
    vi.useFakeTimers();
    try {
      const store = new ProvisionalResultsStore(250);
      const listener = vi.fn();
      store.subscribe(listener);

      store.apply('solve', 1, { frequencies: [100] });
      store.apply('solve', 2, { frequencies: [700] });
      store.apply('solve', 3, { frequencies: [400] });

      expect(listener).toHaveBeenCalledTimes(1);
      expect(store.get('solve')).toMatchObject({ revision: 3, result: { frequencies: [100, 400, 700] } });
      expect(store.getSnapshot().entries.solve).toMatchObject({ revision: 1 });
      vi.advanceTimersByTime(249);
      expect(listener).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(1);
      expect(listener).toHaveBeenCalledTimes(2);
      expect(store.getSnapshot().entries.solve).toMatchObject({ revision: 3, result: { frequencies: [100, 400, 700] } });
    } finally {
      vi.useRealTimers();
    }
  });
});
