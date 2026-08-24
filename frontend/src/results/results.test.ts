import { describe, expect, it, vi } from 'vitest';
import { CompareStore, fetchJobArchiveSnapshot, fetchJobResults, fetchRadiationImpedancePresentation, mergeProvisionalResults, parseFinalResultEnvelope, ProvisionalResultsStore, recombineJobResults, ResultsLruCache, resultsCache, type JobResults, type ResultData } from '../api/results';
import { COMBINED_VIEW } from '../stores/resultView';
import { beamShapeSeries, complexToDb, directivityGrid, directivityIndexSeries, excursionChartSeries, groupDelayValue, impedanceComparable, impedanceSeries, impedanceSubtitle, polarCut, polarMirrorsAcrossAxis, polarSeries, powerResponseMethodCaption, powerResponseSeries, selectResultChannels, splSeries, type NamedResult } from './mappers';
import { combinedChannelId, type ResultPayload } from './types';

/** A channel as the server stamps it from the ingest record's source roles. */
function roled(payload: JobResults, role: string): JobResults {
  return { ...payload, metadata: { ...payload.metadata, role } };
}

function combinedChannel(payload: JobResults, members: string[]): JobResults {
  return {
    ...payload,
    metadata: { ...payload.metadata, combine: { members, crossovers_hz: [1_000] } },
  };
}

function provenance() {
  const digest = 'a'.repeat(64);
  return {
    schema_version: 1,
    wg_version: 'test',
    dependency_shas: {},
    request_sha256: digest,
    geometry_sha256: digest,
    solve_options_sha256: digest,
    request_identity: 'execution',
    execution_request_sha256: digest,
    execution_geometry_sha256: digest,
    execution_solve_options_sha256: digest,
    effective_request_sha256: digest,
    effective_geometry_sha256: digest,
    effective_solve_options_sha256: digest,
    resolved_engine: 'test',
  };
}

function result(offset = 0): JobResults {
  return {
    result_kind: 'parametric',
    result_contract_version: 1,
    client_request_id: null,
    client_metadata: {},
    provenance: provenance(),
    metadata: {},
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

  it('adopts the parametric v1 identity for a result migrated from the original application', async () => {
    resultsCache.clear();
    const { result_kind: _kind, result_contract_version: _version, provenance: _provenance, ...legacy } = result();
    const fetcher = vi.fn(async () => new Response(JSON.stringify(legacy), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    const fetched = await fetchJobResults('migrated-v1', fetcher);
    expect(fetched).toMatchObject({ result_kind: 'parametric', result_contract_version: 1 });
    expect(resultsCache.has('migrated-v1')).toBe(true);
  });

  it.each([
    ['half-declared identity', { ...result(), result_contract_version: undefined }, 'missing result_contract_version'],
    ['unsupported version', { ...result(), result_contract_version: 999 }, 'unsupported result version 999'],
  ])('rejects a %s final envelope without caching it', async (_case, payload, message) => {
    resultsCache.clear();
    const fetcher = vi.fn(async () => new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));

    await expect(fetchJobResults('invalid-contract', fetcher)).rejects.toThrow(message);
    expect(resultsCache.has('invalid-contract')).toBe(false);
  });

  it('accepts parametric v1 and multi-channel v2 final envelopes', () => {
    expect(parseFinalResultEnvelope(result()).result_kind).toBe('parametric');
    const multi = {
      ...result(),
      result_kind: 'multi_channel',
      result_contract_version: 2,
      channels: { hf: { frequencies: [200] } },
      channel_order: ['hf'],
    };
    expect(parseFinalResultEnvelope(multi)).toMatchObject({
      result_kind: 'multi_channel', result_contract_version: 2,
    });
  });

  it('validates archive snapshots before caching their final result', async () => {
    resultsCache.clear();
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      schema_version: 1,
      results: { ...result(), result_contract_version: 999 },
      results_sha256: 'a'.repeat(64),
      mesh_artifact: null,
      pressure_bases: [],
      radiation_impedance: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    await expect(fetchJobArchiveSnapshot('archive-invalid', fetcher))
      .rejects.toThrow('unsupported result version 999');
    expect(resultsCache.has('archive-invalid')).toBe(false);
  });

  it('treats an absent optional radiation artifact as no presentation', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'This job has no passive-cardioid artifact' }),
      { status: 404, headers: { 'Content-Type': 'application/json' } },
    ));
    await expect(fetchRadiationImpedancePresentation('ordinary', fetcher)).resolves.toBeNull();
    expect(fetcher).toHaveBeenCalledWith('/api/radiation-impedance/ordinary/presentation');
  });

  it('prunes deleted primary and overlay job ids', () => {
    const selection = new CompareStore();
    selection.setPrimary('primary');
    selection.toggleOverlay('keep');
    selection.toggleOverlay('deleted');
    selection.prune(new Set(['keep']));
    expect(selection.getSnapshot()).toEqual({ primary: null, overlays: ['keep'], following: true, awaiting: null });
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
    expect(selection.getSnapshot()).toEqual({ primary: 'pinned', overlays: [], following: false, awaiting: null });
  });

  // A pin taken at any point in the session used to outlive every solve that
  // came after it: the run the user had just started finished into a rail that
  // still showed the old result. The submission claims the slot instead, and
  // the claim is only cashed in when that run has something to show.
  it('hands the pinned slot to a submitted run once it has results', () => {
    const selection = new CompareStore();
    selection.setPrimary('pinned');
    selection.awaitRun('fresh');
    expect(selection.getSnapshot()).toMatchObject({ primary: 'pinned', following: false, awaiting: 'fresh' });

    // Jobs messages arrive throughout the solve, and the claimed run is not in
    // the list at all until the server has registered it.
    selection.prune(new Set(['pinned']));
    expect(selection.getSnapshot().awaiting).toBe('fresh');

    selection.followLatest('fresh');
    expect(selection.getSnapshot()).toMatchObject({ primary: 'fresh', following: true, awaiting: null });
  });

  it('drops a claim when a result is picked by hand after the solve started', () => {
    const selection = new CompareStore();
    selection.awaitRun('fresh');
    selection.setPrimary('chosen');
    expect(selection.getSnapshot()).toMatchObject({ primary: 'chosen', following: false, awaiting: null });
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
    const first: ResultData = {
      frequencies: [200],
      directivity: { horizontal: [[[0, 0], [90, -12]]] },
      spl_on_axis: { frequencies: [200], spl: [90], phase_degrees: [0] },
      channels: { hf: { frequencies: [200], impedance: { frequencies: [200], real: [1], imaginary: [0] } } },
      channel_order: ['hf'],
      metadata: { provisional: { completed_frequency_count: 1 } },
    };
    const second: ResultData = {
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
  it('does not classify an ordinary channel literally named combined as a sum', () => {
    const payload: ResultData = {
      frequencies: [],
      channel_order: ['drive-hf', 'combined'],
      channels: { 'drive-hf': roled(result(1), 'HF'), combined: result(2) },
    };

    expect(combinedChannelId(payload)).toBeNull();
    expect(selectResultChannels('job-ordinary', 'Ordinary', payload, COMBINED_VIEW).map(({ id, label }) => ({ id, label })))
      .toEqual([{ id: 'job-ordinary#drive-hf', label: 'Ordinary · HF' }]);
    expect(selectResultChannels('job-ordinary', 'Ordinary', payload, 'combined').map(({ id, label }) => ({ id, label })))
      .toEqual([{ id: 'job-ordinary#combined', label: 'Ordinary · combined' }]);
  });

  it('counts a group delay in periods of the frequency it occurs at', () => {
    // One millisecond is exactly one period of 1 kHz, a tenth of one at 100 Hz,
    // and ten at 10 kHz. Milliseconds pass through untouched.
    expect(groupDelayValue(1, 1_000, 'cycles')).toBe(1);
    expect(groupDelayValue(1, 100, 'cycles')).toBeCloseTo(0.1, 12);
    expect(groupDelayValue(1, 10_000, 'cycles')).toBeCloseTo(10, 12);
    expect(groupDelayValue(0.3, 2_000, 'cycles')).toBeCloseTo(0.6, 12);
    expect(groupDelayValue(-0.25, 4_000, 'cycles')).toBeCloseTo(-1, 12);
    expect(groupDelayValue(0.3, 2_000, 'ms')).toBe(0.3);
  });

  it('contributes only the chosen channel of an imported run', () => {
    const payload: ResultData = {
      frequencies: [],
      channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-mf': roled(result(2), 'MF'), 'drive-hf': roled(result(1), 'HF') },
    };
    expect(selectResultChannels('job-12', 'Run 12', payload, 'drive-mf').map(({ id, label }) => ({ id, label })))
      .toEqual([{ id: 'job-12#drive-mf', label: 'Run 12 · MF' }]);
    // No combined channel and no such view: the first channel stands in, and
    // the label says which, so a comparison never draws an unnamed substitute.
    expect(selectResultChannels('job-12', 'Run 12', payload, 'combined').map(({ label }) => label))
      .toEqual(['Run 12 · HF']);
    expect(selectResultChannels('job-13', 'Run 13', result(), 'combined'))
      .toEqual([{ id: 'job-13', label: 'Run 13', result: result() }]);
  });

  it('appends the members of a combined sum as secondary entries', () => {
    const payload: ResultData = {
      frequencies: [],
      channel_order: ['drive-mf', 'drive-hf', 'combined'],
      channels: {
        'drive-mf': roled(result(2), 'MF'),
        'drive-hf': roled(result(1), 'HF'),
        combined: combinedChannel(result(3), ['drive-mf', 'drive-hf']),
      },
    };
    expect(selectResultChannels('job-9', 'Run 9', payload, 'combined').map(({ label, secondary }) => ({ label, secondary })))
      .toEqual([
        { label: 'Run 9 · Combined', secondary: undefined },
        { label: 'Run 9 · MF', secondary: true },
        { label: 'Run 9 · HF', secondary: true },
      ]);
    // A driver view is that driver alone: the sum is not a second opinion
    // about it, and the members are not comparisons.
    expect(selectResultChannels('job-9', 'Run 9', payload, 'drive-hf').map(({ label }) => label))
      .toEqual(['Run 9 · HF']);
  });

  // The coupled campaign appends its derived output last in `channel_order`.
  // Nothing here special-cases it, which is the point: it is reachable as a
  // view like any other channel.
  it('falls back to the first channel and preserves an empty-channel wrapper', () => {
    const payload: ResultData = {
      frequencies: [],
      channel_order: ['drive-mf', 'drive-port', 'passive_cardioid'],
      channels: { 'drive-mf': result(1), 'drive-port': result(2), passive_cardioid: result(3) },
    };
    expect(selectResultChannels('job-77', 'Run 77', payload, 'passive_cardioid').map(({ id, label }) => ({ id, label })))
      .toEqual([{ id: 'job-77#passive_cardioid', label: 'Run 77 · Cardioid' }]);
    expect(selectResultChannels('job-77', 'Run 77', payload, 'drive-lf').map(({ id }) => id))
      .toEqual(['job-77#drive-mf']);
    const empty: ResultData = { frequencies: [123], channels: {} };
    expect(selectResultChannels('empty', 'Empty', empty, 'combined')).toEqual([{ id: 'empty', label: 'Empty', result: empty }]);
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
    } as ResultData;
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
    } as ResultData;
    expect(directivityIndexSeries(payload)).toEqual([]);
  });

  it('maps the true balloon-integrated power response and keeps unavailable DI cells empty', () => {
    const payload = {
      frequencies: [200, 1_000, 5_000],
      spl_on_axis: { frequencies: [200, 1_000, 5_000], spl: [90, 96, 100] },
      di: { frequencies: [200, 1_000, 5_000], di: [2, null, 9] },
      metadata: { directivity_index: {
        definition: '10log10(reference-axis mean-square pressure / full-sphere mean-square pressure)',
        domain: 'full_sphere',
      } },
    } as ResultPayload;

    expect(powerResponseSeries(payload)).toEqual([expect.objectContaining({
      name: 'full-sphere integral of the solved balloon',
      data: [[200, 88], [1_000, null], [5_000, 91]],
    })]);
    expect(powerResponseMethodCaption({
      ...payload,
      balloon: { frequencies: [], theta_deg: [], phi_deg: [], spl_norm_db: [], hemisphere: true },
    })).toContain('zero-radiation rear hemisphere');
    expect(powerResponseSeries({ ...payload, di: { frequencies: [200], di: [null] } })).toEqual([]);
  });
});

describe('recombineJobResults', () => {
  it('posts the spec, replaces the cache entry, and surfaces server detail', async () => {
    resultsCache.clear();
    const updated = {
      ...result(),
      result_kind: 'multi_channel' as const,
      result_contract_version: 2 as const,
      frequencies: [100],
      channels: { combined: { frequencies: [100] } },
      channel_order: ['combined'],
    };
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe('/api/results/job-9/combine');
      expect(init?.method).toBe('POST');
      expect(JSON.parse(String(init?.body))).toEqual({
        id: 'combined', members: ['mf', 'hf'], crossovers_hz: [900], level_match: true, align: true,
      });
      return new Response(JSON.stringify(updated), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const responseResult = await recombineJobResults('job-9', {
      id: 'combined', members: ['mf', 'hf'], crossovers_hz: [900], level_match: true, align: true,
    }, fetcher as unknown as typeof fetch);
    expect(responseResult).toEqual(updated);
    expect(resultsCache.get('job-9')).toEqual(updated);

    resultsCache.clear();
    const unsupported = vi.fn(async () => new Response(
      JSON.stringify({ ...updated, result_contract_version: 999 }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    await expect(recombineJobResults(
      'job-unsupported',
      { members: ['mf', 'hf'], crossovers_hz: [900] },
      unsupported as unknown as typeof fetch,
    )).rejects.toThrow('unsupported result version 999');
    expect(resultsCache.has('job-unsupported')).toBe(false);

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
    expect(impedanceSubtitle([{ id: 'x', label: 'x', result: { frequencies: [500] } as ResultData }])).toBeNull();
  });

  it('keeps legacy impedance samples that use the top-level frequency grid', () => {
    const legacy = {
      id: 'legacy',
      label: 'legacy',
      result: {
        frequencies: [500],
        impedance: { real: [8], imaginary: [1] },
        metadata: { impedance_units: 'ohms' },
      } as ResultData,
    };
    const empty = {
      id: 'empty', label: 'empty',
      result: { frequencies: [500], impedance: {} } as ResultData,
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
