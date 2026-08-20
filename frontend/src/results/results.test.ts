import { describe, expect, it, vi } from 'vitest';
import { CompareStore, fetchJobResults, mergeProvisionalResults, ProvisionalResultsStore, recombineJobResults, ResultsLruCache, resultsCache, type JobResults } from '../api/results';
import { beamShapeSeries, complexToDb, directivityGrid, directivityIndexSeries, excursionChartSeries, expandResultChannels, impedanceComparable, impedanceSeries, impedanceSubtitle, polarCut, polarMirrorsAcrossAxis, polarSeries, splSeries, type NamedResult } from './mappers';
import type { ResultPayload } from './types';

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

describe('provisional frequency results', () => {
  it('appends frequency-shaped blocks and nested drive channels', () => {
    const first: JobResults = {
      frequencies: [200],
      directivity: { horizontal: [[[0, 0], [90, -12]]] },
      spl_on_axis: { frequencies: [200], spl: [90], phase_degrees: [0] },
      channels: { hf: { frequencies: [200], impedance: { frequencies: [200], real: [1], imaginary: [0] } } },
      channel_order: ['hf'],
      metadata: { provisional: { completed_frequency_count: 1 } },
    };
    const second: JobResults = {
      frequencies: [400],
      directivity: { horizontal: [[[0, 0], [90, -18]]] },
      spl_on_axis: { frequencies: [400], spl: [93], phase_degrees: [10] },
      channels: { hf: { frequencies: [400], impedance: { frequencies: [400], real: [2], imaginary: [.5] } } },
      channel_order: ['hf'],
      metadata: { provisional: { completed_frequency_count: 2 } },
    };

    const merged = mergeProvisionalResults(first, second);
    expect(merged.frequencies).toEqual([200, 400]);
    expect(merged.directivity?.horizontal).toHaveLength(2);
    expect(merged.spl_on_axis?.spl).toEqual([90, 93]);
    expect(merged.channels?.hf.frequencies).toEqual([200, 400]);
    expect(merged.channels?.hf.impedance?.real).toEqual([1, 2]);
    expect((merged.metadata?.provisional as { completed_frequency_count: number }).completed_frequency_count).toBe(2);
    expect(first.frequencies).toEqual([200]);
  });

  it('detects a dropped delta and accepts a full recovery snapshot', () => {
    const store = new ProvisionalResultsStore();
    expect(store.apply('solve', 1, { frequencies: [200] })).toBe(true);
    expect(store.apply('solve', 3, { frequencies: [800] })).toBe(false);
    expect(store.get('solve')?.result.frequencies).toEqual([200]);
    expect(store.apply('solve', 3, { frequencies: [200, 400, 800] }, true)).toBe(true);
    expect(store.get('solve')).toMatchObject({ revision: 3, result: { frequencies: [200, 400, 800] } });
  });
});

describe('chart data mappers', () => {
  it('expands imported drive channels into named comparable results', () => {
    const payload: JobResults = {
      frequencies: [],
      channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-mf': result(2), 'drive-hf': result(1) },
    };
    const expanded = expandResultChannels('job-12', 'Run 12', payload);
    expect(expanded.map(({ id, label }) => ({ id, label }))).toEqual([
      { id: 'job-12#drive-hf', label: 'Run 12 · drive-hf' },
      { id: 'job-12#drive-mf', label: 'Run 12 · drive-mf' },
    ]);
    expect(expandResultChannels('job-13', 'Run 13', result())[0].result).toBeDefined();
  });

  // The coupled campaign appends its derived output last in `channel_order`.
  // It is a channel like any other here, which is the point: nothing about the
  // chart list special-cases it, so it can only go missing if the ordering
  // logic itself breaks.
  it('lists the derived passive-cardioid channel alongside the drive channels', () => {
    const payload: JobResults = {
      frequencies: [],
      channel_order: ['drive-mf', 'drive-port', 'passive_cardioid'],
      channels: { 'drive-mf': result(1), 'drive-port': result(2), passive_cardioid: result(3) },
    };
    expect(expandResultChannels('job-77', 'Run 77', payload).map(({ id, label }) => ({ id, label }))).toEqual([
      { id: 'job-77#drive-mf', label: 'Run 77 · drive-mf' },
      { id: 'job-77#drive-port', label: 'Run 77 · drive-port' },
      { id: 'job-77#passive_cardioid', label: 'Run 77 · passive_cardioid' },
    ]);
  });

  it('appends unordered channels and preserves an empty-channel wrapper', () => {
    const payload: JobResults = {
      frequencies: [],
      channel_order: ['drive-hf'],
      channels: { 'drive-mf': result(2), 'drive-hf': result(1) },
    };
    expect(expandResultChannels('job', 'Run', payload).map(({ id }) => id)).toEqual([
      'job#drive-hf', 'job#drive-mf',
    ]);
    const empty: JobResults = { frequencies: [123], channels: {} };
    expect(expandResultChannels('empty', 'Empty', empty)).toEqual([{ id: 'empty', label: 'Empty', result: empty }]);
  });

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

  it('renders directivity-derived line series as monotone curves without changing their samples', () => {
    const payload = {
      frequencies: [200, 1_000, 5_000],
      di: { frequencies: [200, 1_000, 5_000], di: [2, 7, 9] },
      beam_shape: {
        frequencies: [200, 1_000, 5_000],
        horizontal_beamwidth_deg: [120, 90, 60],
        vertical_beamwidth_deg: [100, 70, 45],
      },
    } as JobResults;
    const [di] = directivityIndexSeries(payload);
    const beam = beamShapeSeries(payload);

    expect(di).toMatchObject({ smooth: 0.32, smoothMonotone: 'x', data: [[200, 2], [1_000, 7], [5_000, 9]] });
    expect(beam).toEqual(expect.arrayContaining([
      expect.objectContaining({ smooth: 0.32, smoothMonotone: 'x', data: [[200, 120], [1_000, 90], [5_000, 60]] }),
    ]));
  });

  it('does not create a DI line when every standard DI sample is unavailable', () => {
    const payload = {
      frequencies: [200, 1_000],
      di: { frequencies: [200, 1_000], di: [null, null] },
    } as JobResults;
    expect(directivityIndexSeries(payload)).toEqual([]);
  });
});

describe('recombineJobResults', () => {
  it('posts the spec, replaces the cache entry, and surfaces server detail', async () => {
    resultsCache.clear();
    const updated = { channels: { combined: { frequencies: [100] } }, channel_order: ['combined'] } as unknown as JobResults;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe('/api/results/job-9/combine');
      expect(init?.method).toBe('POST');
      expect(JSON.parse(String(init?.body))).toEqual({
        id: 'combined', members: ['mf', 'hf'], crossovers_hz: [900], level_match: true, align: true,
      });
      return new Response(JSON.stringify(updated), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const result = await recombineJobResults('job-9', {
      id: 'combined', members: ['mf', 'hf'], crossovers_hz: [900], level_match: true, align: true,
    }, fetcher as unknown as typeof fetch);
    expect(result).toEqual(updated);
    expect(resultsCache.get('job-9')).toEqual(updated);

    const failing = vi.fn(async () => new Response(
      JSON.stringify({ detail: 'combine crossovers_hz [5000.0] lie outside the solved band [100, 1000] Hz' }),
      { status: 422 },
    ));
    await expect(recombineJobResults('job-9', { members: ['mf', 'hf'], crossovers_hz: [5000] }, failing as unknown as typeof fetch))
      .rejects.toThrow('outside the solved band');
    resultsCache.clear();
  });
});

describe('polar cut mirroring', () => {
  const cut = [[[0, 0], [45, -3], [90, -12]]];
  const oneSided = (quadrants?: number) => ({
    frequencies: [1_000],
    directivity: { horizontal: cut, vertical: cut },
    metadata: quadrants === undefined ? {} : { symmetry: { resolved_quadrants: quadrants } },
  }) as unknown as ResultPayload;

  // The whole table, both planes, because mirroring the wrong cut fabricates a
  // radiation pattern the user cannot tell from computed data. The pairing is
  // the server's: `_PLANE_BY_QUADRANTS` reduces 12 about y=0 and 14 about x=0,
  // and the observation frame puts the vertical cut in the yz plane (crossing
  // y=0) and the horizontal cut in the xz plane (crossing x=0). Only the cut
  // that crosses the reduced plane inherits its symmetry.
  it.each([
    [1, 'horizontal', true],
    [1, 'vertical', true],
    [12, 'horizontal', false],
    [12, 'vertical', true],
    [14, 'horizontal', true],
    [14, 'vertical', false],
    [1234, 'horizontal', false],
    [1234, 'vertical', false],
  ] as const)('quadrants %i mirrors the %s cut: %s', (quadrants, plane, expected) => {
    expect(polarMirrorsAcrossAxis(oneSided(quadrants), plane)).toBe(expected);
    expect(polarCut(oneSided(quadrants), 0, plane)).toEqual(expected
      ? [[-12, -90], [-3, -45], [0, 0], [-3, 45], [-12, 90]]
      : [[0, 0], [-3, 45], [-12, 90]]);
  });

  it('mirrors nothing for an untagged or unrecognised symmetry', () => {
    expect(polarMirrorsAcrossAxis(oneSided(), 'horizontal')).toBe(false);
    expect(polarMirrorsAcrossAxis(oneSided(), 'vertical')).toBe(false);
    // A quadrant value outside the table is a contract we have not verified;
    // it must fall through to "do not mirror" rather than to a default plane.
    expect(polarMirrorsAcrossAxis(oneSided(13), 'horizontal')).toBe(false);
    expect(polarMirrorsAcrossAxis(oneSided(13), 'vertical')).toBe(false);
  });

  it('reads the solve symmetry off a CAD envelope when the channel does not repeat it', () => {
    // An imported result records symmetry once on the wrapper; without this
    // join every CAD-import polar stayed one-sided.
    const channel = oneSided();
    const wrapper = { frequencies: [], metadata: { symmetry: { resolved_quadrants: 12 } } } as unknown as ResultPayload;
    expect(polarMirrorsAcrossAxis(channel, 'vertical', wrapper)).toBe(true);
    expect(polarMirrorsAcrossAxis(channel, 'horizontal', wrapper)).toBe(false);
    expect(polarCut(channel, 0, 'vertical', wrapper).map(([, angle]) => angle))
      .toEqual([-90, -45, 0, 45, 90]);
  });

  it('does not mirror a cut that already spans both sides', () => {
    const twoSided = {
      frequencies: [1_000],
      directivity: { horizontal: [[[-45, -3], [0, 0], [45, -3]]] },
      metadata: { symmetry: { resolved_quadrants: 1 } },
    } as unknown as ResultPayload;
    expect(polarCut(twoSided, 0, 'horizontal')).toEqual([[-3, -45], [0, 0], [-3, 45]]);
  });
});

describe('impedance comparison subtitle', () => {
  const withImpedance = (id: string, electrical: boolean): NamedResult => ({
    id,
    label: id,
    result: {
      frequencies: [500],
      impedance: { frequencies: [500], real: [8], imaginary: [1] },
      metadata: electrical
        ? { impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance' }
        : { impedance_units: 'Z/(rho*c)', impedance_quantity: 'specific_acoustic_impedance' },
    } as unknown as JobResults,
  });

  it('names the runs left off the axis and why', () => {
    // Dropping them silently reads as a solve that failed rather than as two
    // quantities that cannot share a scale.
    expect(impedanceSubtitle([withImpedance('a', true), withImpedance('b', false), withImpedance('c', false)]))
      .toBe('2 Z/ρc runs hidden · cannot share a Ω axis');
    expect(impedanceSubtitle([withImpedance('a', false), withImpedance('b', true)]))
      .toBe('1 Ω run hidden · cannot share a Z/ρc axis');
  });

  it('says nothing when every run shares the axis, or when there is no impedance at all', () => {
    expect(impedanceSubtitle([withImpedance('a', false), withImpedance('b', false)])).toBeNull();
    expect(impedanceSubtitle([{ id: 'x', label: 'x', result: { frequencies: [500] } as JobResults }])).toBeNull();
  });

  it('keeps legacy impedance samples that use the top-level frequency grid', () => {
    const legacy = {
      id: 'legacy',
      label: 'legacy',
      result: {
        frequencies: [500],
        impedance: { real: [8], imaginary: [1] },
        metadata: { impedance_units: 'ohms' },
      } as JobResults,
    };
    const empty = {
      id: 'empty', label: 'empty',
      result: { frequencies: [500], impedance: {} } as JobResults,
    };

    expect(impedanceComparable([legacy, empty])).toMatchObject({
      items: [legacy], units: { electrical: true }, excluded: 0,
    });
  });
});

describe('excursion Xmax reference line', () => {
  it('rounds the spec float instead of printing it verbatim', () => {
    const traces = excursionChartSeries({
      frequencies: [100, 200],
      metadata: {
        driver: {
          spec: { xmax_mm: 4.500000000000001 },
          cone_excursion_mm: { frequencies: [100, 200], values: [1, 2], peak_mm: 2 },
        },
      },
    } as unknown as ResultPayload);
    expect(traces.map(({ name }) => name)).toEqual(['Excursion', 'Xmax 4.5 mm']);
  });
});
