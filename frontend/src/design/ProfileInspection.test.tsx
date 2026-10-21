import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixtures from '../../../shared/profile-inspection/mesher-profiles.json';
import { profileSamples } from '../api/profileSamples';
import { inspectProfileSamples } from '../api/designIo';
import { readChartTokens } from '../results/EChart';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { FreeformProfileInspection, ProfileInspection, profileChartOption } from './ProfileInspection';

vi.mock('../results/EChart', async (original) => ({
  ...await original<typeof import('../results/EChart')>(),
  EChart: ({ label, option }: { label: string; option: unknown }) => <div role="img" aria-label={label} data-option={JSON.stringify(option)} />,
}));

function series(option: ReturnType<typeof profileChartOption>) {
  return option.series as { name: string; data: number[][]; lineStyle: { color: string } }[];
}

describe('generated bore profile coordinates', () => {
  it.each(fixtures)('plots mesher-exported $name coordinates in physical mm', (fixture) => {
    const samples = profileSamples(fixture.csv);
    for (const axis of ['H', 'V'] as const) {
      expect(samples[axis]).toHaveLength(fixture.expected[axis].length);
      samples[axis].forEach((point, index) => {
        expect(point.z).toBeCloseTo(fixture.expected[axis][index].z, 4);
        expect(point.r).toBeCloseTo(fixture.expected[axis][index].r, 4);
      });
    }
    const tokens = readChartTokens();
    const option = profileChartOption(samples, 'both', 420, 260, tokens);
    for (const axis of [option.xAxis, option.yAxis]) {
      expect(axis).toMatchObject({
        axisLine: { lineStyle: { color: tokens.spine ?? tokens.grid } },
        axisTick: { lineStyle: { color: tokens.muted } },
        axisLabel: { color: tokens.muted },
        splitLine: { lineStyle: { color: tokens.grid } },
        minorSplitLine: { lineStyle: { color: tokens.gridMinor } },
      });
    }
    const traces = series(option);
    expect(traces).toHaveLength(fixture.name === 'equal' ? 1 : 2);
    expect(traces[0].name).toBe(fixture.name === 'equal' ? 'H = V' : 'H');
    expect(traces[0].data).toEqual(samples.H.map(({ z, r }) => [z, r]));
    const grid = option.grid as { width: number; height: number };
    const x = option.xAxis as { min: number; max: number };
    const y = option.yAxis as { min: number; max: number };
    expect(grid.width / (x.max - x.min)).toBeCloseTo(grid.height / (y.max - y.min), 12);
    for (const plane of ['H', 'V'] as const) {
      const single = series(profileChartOption(samples, plane, 900, 260, readChartTokens()));
      expect(single).toHaveLength(1);
      expect(single[0].name).toBe(plane);
      expect(single[0].data).toEqual(samples[plane].map(({ z, r }) => [z, r]));
      expect(single[0].lineStyle.color).toBe(readChartTokens().series[plane === 'H' ? 0 : 1]);
    }
  });

  it('posts the serialized scaled design and propagates the abort signal', async () => {
    const design = { ...designForFamily('FREEFORM'), scale: 1.7 };
    const controller = new AbortController();
    const fetcher = vi.fn(async () => new Response(fixtures[4].csv));
    expect(await inspectProfileSamples(design, 42, controller.signal, fetcher)).toEqual(profileSamples(fixtures[4].csv));
    expect(fetcher).toHaveBeenCalledWith('/api/export/profiles?kind=profiles', expect.objectContaining({ signal: controller.signal, method: 'POST' }));
    const body = JSON.parse((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    expect(body.designRevision).toBe(42);
    expect(body.design.scale).toBe(1.7);
  });
});

describe('profile freshness', () => {
  let root: Root;
  let host: HTMLDivElement;
  let requests: { signal: AbortSignal; resolve: (value: Response) => void }[];
  const design = designForFamily('FREEFORM');
  const render = (identity: string, revision: number, invalidDraft = false) => act(() => root.render(
    <ProfileInspection design={design} identity={identity} revision={revision} invalidDraft={invalidDraft} />,
  ));
  const tick = async () => { await act(async () => vi.advanceTimersByTimeAsync(300)); };
  const respond = async (index: number, body: string, status = 200) => {
    await act(async () => { requests[index].resolve(new Response(body, { status })); });
  };
  const trace = () => JSON.parse(host.querySelector('[data-option]')!.getAttribute('data-option')!);

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers(); requests = [];
    vi.stubGlobal('fetch', vi.fn((_url, init) => new Promise<Response>((resolve) => requests.push({ signal: init.signal, resolve }))));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.useRealTimers(); });

  it.each([1, 1.7, 0.5])('labels scaled plot coordinates when Scale is %s', (scale) => {
    act(() => root.render(<ProfileInspection design={{ ...design, scale }} identity="a" revision={1} />));
    const caption = host.querySelector('p')!.textContent;
    expect(caption).toContain('Generated bore profile — scaling, morph and supported extensions applied. Inspection only.');
    if (scale === 1) expect(caption).not.toContain('Plot in scaled mm');
    else expect(caption).toContain(`Plot in scaled mm (Scale = ${scale}).`);
  });

  it('debounces edits, aborts superseded requests and drops late edit responses', async () => {
    render('a', 1); render('a', 2);
    expect(requests).toHaveLength(0);
    await tick(); expect(requests).toHaveLength(1);
    render('a', 3); expect(requests[0].signal.aborted).toBe(true);
    await tick(); await respond(1, fixtures[1].csv);
    const accepted = trace();
    await respond(0, fixtures[0].csv);
    expect(trace()).toEqual(accepted);
    expect(host.textContent).toContain('H and V profiles');
  });

  it('hides the old document immediately and never accepts its late response after opening another', async () => {
    render('a', 1); await tick(); await respond(0, fixtures[0].csv);
    render('a', 2); await tick();
    render('b', 1);
    expect(host.querySelector('[role="img"]')).toBeNull();
    await respond(1, fixtures[1].csv);
    expect(host.querySelector('[role="img"]')).toBeNull();
    await tick(); await respond(2, fixtures[0].csv);
    expect(host.textContent).toContain('H = V');
  });

  it('dims last-valid data while updating, reports build errors and recovers', async () => {
    render('a', 1); await tick(); await respond(0, fixtures[0].csv);
    render('a', 2);
    expect(host.querySelector('[data-stale="true"]')).not.toBeNull();
    expect(host.textContent).toContain('Updating… — last valid');
    await tick(); await respond(1, JSON.stringify({ detail: 'Invalid corner radius' }), 422);
    expect(host.textContent).toContain('Invalid: Invalid corner radius — last valid profile');
    render('a', 3); await tick(); await respond(2, fixtures[1].csv);
    expect(host.querySelector('[data-stale]')).toBeNull();
    render('a', 3, true);
    expect(host.textContent).toContain('Invalid input — last valid');
  });

  it('shows an initial error without a curve and ignores results after unmount', async () => {
    render('a', 1); await tick(); await respond(0, JSON.stringify({ detail: 'Bad profile' }), 422);
    expect(host.textContent).toContain('Invalid: Bad profile');
    expect(host.querySelector('[role="img"]')).toBeNull();
    render('a', 2); await tick();
    act(() => root.render(null));
    expect(requests[1].signal.aborted).toBe(true);
    await respond(1, fixtures[0].csv);
    expect(host.querySelector('[role="img"]')).toBeNull();
  });

  it('guards a store document replacement even before React observes the new generation', async () => {
    resetDesignStore(); useDesignStore.getState().replaceDocument(design);
    act(() => root.render(<FreeformProfileInspection />));
    await tick();
    await act(async () => {
      // Equal revisions deliberately: document identity must still reject it.
      useDesignStore.getState().replaceDocument(design, { revision: useDesignStore.getState().designRevision });
      requests[0].resolve(new Response(fixtures[1].csv));
    });
    expect(host.querySelector('[role="img"]')).toBeNull();
    await tick(); await respond(1, fixtures[0].csv);
    expect(host.textContent).toContain('H = V');
  });
});
