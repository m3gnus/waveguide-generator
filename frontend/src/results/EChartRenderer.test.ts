import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';

const setOption = vi.fn();
const dispose = vi.fn();
const resize = vi.fn();

vi.mock('echarts/core', () => ({
  use: () => undefined,
  init: () => ({ setOption, dispose, resize }),
}));
vi.mock('echarts/charts', () => ({ CustomChart: {}, HeatmapChart: {}, LineChart: {} }));
vi.mock('echarts/components', () => ({
  DataZoomComponent: {}, GridComponent: {}, LegendComponent: {},
  TooltipComponent: {}, VisualMapComponent: {},
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

const { EChartRenderer } = await import('./EChartRenderer');

describe('interactive chart updates', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    setOption.mockClear(); dispose.mockClear(); resize.mockClear();
    globalThis.ResizeObserver ??= class { observe() {} disconnect() {} unobserve() {} } as never;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); });

  function render(option: EChartsOption) {
    act(() => root.render(createElement(EChartRenderer, { option, label: 'chart' })));
  }

  it('redraws outright, so a retained dataZoom window cannot clip new data', () => {
    render({ series: [{ type: 'line', data: [[100, 1]] }] });
    render({ series: [{ type: 'line', data: [[100, 2]] }] });
    expect(setOption).toHaveBeenCalledTimes(2);
    for (const [, settings] of setOption.mock.calls) expect(settings).toMatchObject({ notMerge: true });
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

  it('disposes its instance when the card goes away', () => {
    render({ series: [] });
    act(() => root.unmount());
    expect(dispose).toHaveBeenCalledTimes(1);
    root = createRoot(host);
  });
});
