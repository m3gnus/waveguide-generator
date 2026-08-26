import { CustomChart, HeatmapChart, LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  PolarComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { EChartsOption } from 'echarts';
import { chartGutter, inAxisGutter, rulerLabel } from './chartRuler';

echarts.use([
  LineChart,
  HeatmapChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  // The polar response chart is the only user; without it a polar option
  // renders as an empty card rather than failing loudly.
  PolarComponent,
  CanvasRenderer,
]);

/**
 * Keep thin engineering traces anti-aliased on ordinary 1x displays.
 *
 * ECharts otherwise sizes its backing canvas at exactly one physical pixel per
 * CSS pixel there, which makes diagonal directivity contours visibly stair-step
 * compared with the high-DPI PNG renderer. A 2x floor is enough to remove that
 * effect without the memory cost of letting unusually large ratios run free.
 */
export function chartPixelRatio(deviceRatio = globalThis.devicePixelRatio || 1): number {
  return Math.min(3, Math.max(2, deviceRatio));
}

interface AxisRuler { y: number; left: number; width: number; text: string | null }

export function EChartRenderer({ option, label, live = false }: { option: EChartsOption; label: string; live?: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const [ruler, setRuler] = useState<AxisRuler | null>(null);

  useEffect(() => {
    if (!host.current) return;
    chart.current = echarts.init(host.current, undefined, {
      renderer: 'canvas',
      useDirtyRect: true,
      devicePixelRatio: chartPixelRatio(),
    });
    // A ruler drawn for the old card size would keep its pixel row while the
    // axis under it moves, which is worse than no ruler: the next pointer move
    // restores it against the axis it actually belongs to.
    const observer = new ResizeObserver(() => {
      chart.current?.resize();
      setRuler(null);
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  // Every call here redraws the chart from nothing, which is a visible flash.
  // That is correct for a genuinely new result and wrong for anything else, so
  // the discipline this depends on lives upstream: `option` must keep its
  // identity while the data behind it has not changed. It used to churn on
  // every jobs message, which is what made a running solve flicker.
  //
  // Merging instead of replacing was tried and reverted: `replaceMerge` keeps
  // the dataZoom component across the swap, and its retained window then
  // clipped the incoming series -- the SPL and impedance panels drew the first
  // fifth of their data stretched across the whole card.
  useEffect(() => {
    // ECharts otherwise animates every wholesale replacement. During a solve
    // another snapshot can arrive before that transition ends, leaving the
    // main thread continuously tweening charts whose data is already stale.
    // The completed snapshot retains the normal transition.
    const renderedOption = live ? { ...option, animation: false, animationDuration: 0 } : option;
    chart.current?.setOption(renderedOption, { notMerge: true, lazyUpdate: true });
  }, [live, option]);

  // The gutter ruler. `convertFromPixel` is the chart's own axis mapping, so a
  // zoomed or auto-extended axis reads correctly without tracking its extent
  // here; on the mocked instance the interactive tests use it is simply absent,
  // and the line then draws without a readout rather than throwing.
  const track = useCallback((event: { clientX: number; clientY: number }) => {
    const element = frame.current;
    if (!element) return;
    const bounds = element.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const gutter = chartGutter(option, bounds.width, bounds.height);
    if (!gutter || !inAxisGutter(gutter, x, y)) {
      setRuler((current) => (current ? null : current));
      return;
    }
    const instance = chart.current;
    const text = instance?.convertFromPixel
      ? rulerLabel(option, gutter, y, (pixel) => instance.convertFromPixel({ yAxisIndex: 0 }, pixel) as number | null)
      : null;
    setRuler({ y, left: gutter.left, width: gutter.right - gutter.left, text });
  }, [option]);

  return <div
    ref={frame}
    className="chart-frame"
    data-ruler={ruler ? 'on' : undefined}
    style={{ position: 'relative', width: '100%', height: '100%', minHeight: 0 }}
    onMouseMove={track}
    onMouseLeave={() => setRuler(null)}
  >
    <div ref={host} role="img" aria-label={label} style={{ width: '100%', height: '100%', minHeight: 0 }} />
    {ruler && <div className="chart-ruler" style={{ top: ruler.y, left: ruler.left, width: Math.max(0, ruler.width) }}>
      {ruler.text && <span>{ruler.text}</span>}
    </div>}
  </div>;
}
