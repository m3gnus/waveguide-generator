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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((settle, fail) => { resolve = settle; reject = fail; });
  return { promise, resolve, reject };
}

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
      angle_guide_step: 0,
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
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({ theme: 'console', frequencies: [500, 1_000] });
  });

  // The default export theme follows the window rather than naming a theme,
  // so a figure taken from a light interface does not come back on a dark
  // ground -- and the chart on screen and the chart in the file agree.
  it('resolves the default export theme from the interface theme', () => {
    document.documentElement.dataset.theme = 'light';
    expect(buildCanonicalDirectivityRequest(result, preferencesStore.getSnapshot(), 'horizontal').payload.theme).toBe('vellum');
    document.documentElement.dataset.theme = 'dark';
    expect(buildCanonicalDirectivityRequest(result, preferencesStore.getSnapshot(), 'horizontal').payload.theme).toBe('console');
    delete document.documentElement.dataset.theme;
  });

  it('deduplicates identical render requests', async () => {
    const request = buildCanonicalDirectivityRequest(result, preferencesStore.getSnapshot(), 'vertical');
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 }));
    await Promise.all([fetchCanonicalPlot(request, fetcher), fetchCanonicalPlot(request, fetcher)]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not let an evicted failed request delete a newer request for the same key', async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    let targetCalls = 0;
    const fetcher = vi.fn<typeof fetch>((_input, init) => {
      const payload = JSON.parse(String(init?.body)) as { cacheRace?: string };
      if (payload.cacheRace === 'target') {
        targetCalls += 1;
        return targetCalls === 1 ? first.promise : second.promise;
      }
      return Promise.resolve(new Response(JSON.stringify({ image: `data:${payload.cacheRace}` }), { status: 200 }));
    });
    const target = { endpoint: '/api/render-directivity' as const, payload: { cacheRace: 'target' } };
    const failed = fetchCanonicalPlot(target, fetcher).catch((error: unknown) => error);
    for (let index = 0; index < 32; index += 1) {
      await fetchCanonicalPlot({ endpoint: '/api/render-directivity', payload: { cacheRace: `filler-${index}` } }, fetcher);
    }
    const newer = fetchCanonicalPlot(target, fetcher);
    first.reject(new Error('old renderer failed'));
    await failed;
    const deduplicated = fetchCanonicalPlot(target, fetcher);

    expect(targetCalls).toBe(2);
    second.resolve(new Response(JSON.stringify({ image: 'data:newer' }), { status: 200 }));
    await expect(Promise.all([newer, deduplicated])).resolves.toEqual(['data:newer', 'data:newer']);
  });
});
