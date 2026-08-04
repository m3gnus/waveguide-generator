import { beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import type { ResultPayload } from './types';
import {
  buildCanonicalChartRequest,
  buildCanonicalDirectivityRequest,
  clearCanonicalPlotCacheForTests,
  fetchCanonicalPlot,
} from './CanonicalPlot';

const result: ResultPayload = {
  frequencies: [500, 1_000],
  spl_on_axis: { frequencies: [500, 1_000], spl: [90, 91], phase_degrees: [0, 5] },
  di: { frequencies: [500, 1_000], di: { horizontal: [4, 6] } },
  impedance: { frequencies: [500, 1_000], real: [1, 1.1], imaginary: [0, 0.1] },
  directivity: {
    horizontal: [[[0, 0], [90, -9]], [[0, 0], [90, -12]]],
    vertical: [[[0, 0], [90, -6]], [[0, 0], [90, -10]]],
  },
};

describe('canonical HornLab plots', () => {
  beforeEach(() => {
    preferencesStore.resetForTests();
    clearCanonicalPlotCacheForTests();
  });

  it('requests a single directivity plane with the selected theme, reference contour, and level', () => {
    const preferences = { ...preferencesStore.getSnapshot(), chartTheme: 'hornlab', mapReference: -9 as const };
    const request = buildCanonicalDirectivityRequest(result, preferences, 'horizontal', { result, label: 'baseline' });
    expect(request.endpoint).toBe('/api/render-directivity');
    expect(request.payload).toMatchObject({
      frequencies: [500, 1_000],
      reference_level: -9,
      theme: 'hornlab',
      reference_label: 'baseline',
      directivity: { horizontal: result.directivity?.horizontal },
      reference_directivity: { horizontal: result.directivity?.horizontal },
    });
    expect(request.payload.directivity).not.toHaveProperty('vertical');
  });

  it('uses the canonical multi-chart renderer for line plots and reads the named image', async () => {
    const request = buildCanonicalChartRequest('frequency_response', result, preferencesStore.getSnapshot());
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ charts: { frequency_response: 'data:image/png;base64,AQ==' } }), { status: 200 }));
    await expect(fetchCanonicalPlot(request, fetcher)).resolves.toBe('data:image/png;base64,AQ==');
    expect(String(fetcher.mock.calls[0][0])).toBe('/api/render-charts');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({ theme: 'hornlab', frequencies: [500, 1_000] });
  });

  it('deduplicates identical render requests', async () => {
    const request = buildCanonicalDirectivityRequest(result, preferencesStore.getSnapshot(), 'vertical');
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 }));
    await Promise.all([fetchCanonicalPlot(request, fetcher), fetchCanonicalPlot(request, fetcher)]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
