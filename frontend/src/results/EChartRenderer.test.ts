import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';

const setOption = vi.fn();
const dispose = vi.fn();
const resize = vi.fn();
const init = vi.fn(() => ({ setOption, dispose, resize }));

vi.mock('echarts/core', () => ({
  use: () => undefined,
  init,
}));
vi.mock('echarts/charts', () => ({ CustomChart: {}, HeatmapChart: {}, LineChart: {} }));
vi.mock('echarts/components', () => ({
  DataZoomComponent: {}, GridComponent: {}, LegendComponent: {},
  PolarComponent: {}, TooltipComponent: {}, VisualMapComponent: {},
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

const { EChartRenderer, chartPixelRatio } = await import('./EChartRenderer');

describe('interactive chart updates', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    setOption.mockClear(); dispose.mockClear(); resize.mockClear(); init.mockClear();
    globalThis.ResizeObserver ??= class { observe() {} disconnect() {} unobserve() {} } as never;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); });

  function render(option: EChartsOption, live = false) {
    act(() => root.render(createElement(EChartRenderer, { option, label: 'chart', live })));
  }

  it('redraws outright, so a retained dataZoom window cannot clip new data', () => {
    render({ series: [{ type: 'line', data: [[100, 1]] }] });
    render({ series: [{ type: 'line', data: [[100, 2]] }] });
    expect(setOption).toHaveBeenCalledTimes(2);
    for (const [, settings] of setOption.mock.calls) expect(settings).toMatchObject({ notMerge: true });
  });

  it('uses a high-density backing canvas so thin contours stay anti-aliased', () => {
    expect(chartPixelRatio(1)).toBe(2);
    expect(chartPixelRatio(2.5)).toBe(2.5);
    expect(chartPixelRatio(4)).toBe(3);
    render({ series: [] });
    expect(init).toHaveBeenCalledWith(expect.any(HTMLElement), undefined, expect.objectContaining({
      renderer: 'canvas',
      devicePixelRatio: 2,
    }));
  });

  it('does not touch the chart while the option object is unchanged', () => {
    // The redraw above is a visible flash, so the panel must hold one option
    // identity for as long as the data behind it is the same -- a solve in
    // flight re-renders it several times a second.
    const option: EChartsOption = { series: [{ type: 'line', data: [[100, 1]] }] };
    render(option);
    render(option);
    render(option);
    expect(setOption).toHaveBeenCalledTimes(1);
  });

  it('does not animate replacements while live snapshots are arriving', () => {
    const option: EChartsOption = { animationDuration: 180, series: [{ type: 'line', data: [[100, 1]] }] };
    render(option, true);
    expect(setOption).toHaveBeenCalledWith(
      expect.objectContaining({ animation: false, animationDuration: 0 }),
      expect.objectContaining({ notMerge: true, lazyUpdate: true }),
    );
    expect(option).toMatchObject({ animationDuration: 180 });
  });

  it('disposes its instance when the card goes away', () => {
    render({ series: [] });
    act(() => root.unmount());
    expect(dispose).toHaveBeenCalledTimes(1);
    root = createRoot(host);
  });
});
