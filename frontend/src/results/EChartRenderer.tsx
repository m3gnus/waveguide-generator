import { CustomChart, HeatmapChart, LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { useEffect, useRef } from 'react';
import type { EChartsOption } from 'echarts';

echarts.use([
  LineChart,
  HeatmapChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export function EChartRenderer({ option, label }: { option: EChartsOption; label: string }) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!host.current) return;
    chart.current = echarts.init(host.current, undefined, { renderer: 'canvas', useDirtyRect: true });
    const observer = new ResizeObserver(() => chart.current?.resize());
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
    chart.current?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  return <div ref={host} role="img" aria-label={label} style={{ width: '100%', height: '100%', minHeight: 0 }} />;
}
