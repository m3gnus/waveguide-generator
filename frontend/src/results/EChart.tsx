import * as echarts from 'echarts';
import { useEffect, useRef, useState } from 'react';
import type { EChartsOption } from 'echarts';

export interface ChartTokens {
  foreground: string;
  muted: string;
  grid: string;
  gridMinor: string;
  accent: string;
  series: string[];
  colormap: string[];
}

function css(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function readChartTokens(): ChartTokens {
  return {
    foreground: css('--fg1', '#d8dde8'),
    muted: css('--fg3', '#7f8796'),
    grid: css('--grid', '#1a212d'),
    gridMinor: css('--grid-min', '#131822'),
    accent: css('--acc', '#37c8df'),
    series: ['--s0', '--s1', '--s2', '--s3', '--s4'].map((name) => css(name, '#37c8df')),
    colormap: Array.from({ length: 8 }, (_, index) => css(`--cm${index}`, '#1a4673')),
  };
}

export function useChartTokens(): ChartTokens {
  const [tokens, setTokens] = useState(readChartTokens);
  useEffect(() => {
    const observer = new MutationObserver(() => setTokens(readChartTokens()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
    return () => observer.disconnect();
  }, []);
  return tokens;
}

export function EChart({ option, label }: { option: EChartsOption; label: string }) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!host.current) return;
    chart.current = echarts.init(host.current, undefined, { renderer: 'canvas' });
    const observer = new ResizeObserver(() => chart.current?.resize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  return <div ref={host} role="img" aria-label={label} style={{ width: '100%', height: '100%', minHeight: 0 }} />;
}

