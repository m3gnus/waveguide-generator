import * as echarts from 'echarts/core';
import type { EChartsOption, SetOptionOpts } from 'echarts';
import '../src/results/EChartRenderer';
import { heatmapOption, lineOption } from '../src/shell/ResultsPanel';
import type { ChartTokens } from '../src/results/EChart';
import type { ResultPayload } from '../src/results/types';

declare global {
  interface Window { __ECHARTS_BENCHMARK__?: BenchmarkReport; }
}

const tokens: ChartTokens = {
  foreground: '#d8dde8', muted: '#7f8796', grid: '#1a212d', gridMinor: '#141a24', accent: '#4aa3df',
  series: ['#4aa3df', '#df8b4a', '#5bb98c', '#aa7ee0'],
  colormap: ['#0b1d33', '#1a4673', '#2f7ab8', '#59a7d8', '#8fc9e8', '#bfe0f2', '#e2f1fa', '#ffffff'],
};

interface Sample { synchronousMs: number; paintedMs: number; }
interface Summary { median: number; p95: number; max: number; }
interface StrategyReport {
  heatmapPaintedMs: Summary;
  dashboardPaintedMs: Summary;
  dashboardSynchronousMs: Summary;
  zoomAfterUpdate: [number, number];
}
interface BenchmarkReport {
  environment: { userAgent: string; devicePixelRatio: number; chartSize: string; publicationBudgetMs: number; };
  workload: { snapshots: number; maximumHeatmapCells: number; lineCharts: number; };
  strategies: Record<string, StrategyReport>;
}

function payload(frequencyCount: number, revision: number): ResultPayload {
  const frequencies = Array.from({ length: frequencyCount }, (_, index) => 400 * (40 ** (index / Math.max(1, frequencyCount - 1))));
  const angles = Array.from({ length: 37 }, (_, index) => index * 5);
  return {
    frequencies,
    directivity: {
      horizontal: frequencies.map((frequency, frequencyIndex) => angles.map((angle) => [
        angle,
        -Math.abs(angle - 90) / (5.5 + frequencyIndex / 80) + Math.sin(frequency / 1400 + revision / 5) * .4,
      ] as [number, number])),
    },
  } as unknown as ResultPayload;
}

function options(frequencyCount: number, revision: number): EChartsOption[] {
  const result = payload(frequencyCount, revision);
  const lineSeries = (seriesIndex: number) => ({
    id: `line-${seriesIndex}`,
    name: `Trace ${seriesIndex + 1}`,
    type: 'line' as const,
    showSymbol: false,
    data: result.frequencies.map((frequency, index) => [frequency, 85 + seriesIndex * 3 + Math.sin(index / 4 + revision / 6 + seriesIndex)]),
  });
  const line = lineOption(Array.from({ length: 4 }, (_, index) => lineSeries(index)), tokens, 'dB', 'regular');
  return [
    heatmapOption(result, tokens, 'horizontal', -6, 'regular', true),
    line,
    lineOption(Array.from({ length: 2 }, (_, index) => lineSeries(index)), tokens, 'Ω', 'regular'),
    lineOption([lineSeries(0)], tokens, 'mm', 'regular'),
  ].map((option) => ({ ...option, animation: false, animationDuration: 0 }));
}

function quantile(values: number[], fraction: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * fraction))];
}

function summary(values: number[]): Summary {
  return {
    median: Number(quantile(values, .5).toFixed(2)),
    p95: Number(quantile(values, .95).toFixed(2)),
    max: Number(Math.max(...values).toFixed(2)),
  };
}

async function update(chart: echarts.ECharts, option: EChartsOption, settings: SetOptionOpts): Promise<Sample> {
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  const start = performance.now();
  const painted = new Promise<void>((resolve) => {
    const finish = () => { chart.off('finished', finish); resolve(); };
    chart.on('finished', finish);
  });
  chart.setOption(option, settings);
  const synchronousMs = performance.now() - start;
  await painted;
  return { synchronousMs, paintedMs: performance.now() - start };
}

async function updateDashboard(charts: echarts.ECharts[], current: EChartsOption[], settings: SetOptionOpts): Promise<Sample> {
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  const start = performance.now();
  let synchronousMs = 0;
  const painted = charts.map((chart) => new Promise<void>((resolve) => {
    const finish = () => { chart.off('finished', finish); resolve(); };
    chart.on('finished', finish);
  }));
  for (let index = 0; index < charts.length; index += 1) {
    const optionStart = performance.now();
    charts[index].setOption(current[index], settings);
    synchronousMs += performance.now() - optionStart;
  }
  await Promise.all(painted);
  return { synchronousMs, paintedMs: performance.now() - start };
}

function createCharts(): echarts.ECharts[] {
  const charts = document.querySelector('#charts')!;
  return Array.from({ length: 4 }, () => {
    const host = document.createElement('div');
    host.className = 'chart';
    charts.append(host);
    return echarts.init(host, undefined, { renderer: 'canvas', useDirtyRect: true, devicePixelRatio: 2 });
  });
}

async function runStrategy(name: string, settings: SetOptionOpts): Promise<StrategyReport> {
  const charts = createCharts();
  const initial = options(8, 0);
  for (let index = 0; index < charts.length; index += 1) await update(charts[index], initial[index], { notMerge: true, lazyUpdate: true });
  charts[1].dispatchAction({ type: 'dataZoom', start: 30, end: 70 });

  const heatmap: Sample[] = [];
  const dashboard: Sample[] = [];
  const counts = Array.from({ length: 13 }, (_, index) => 12 + index * 4);
  for (let repeat = 0; repeat < 3; repeat += 1) {
    for (let index = 0; index < counts.length; index += 1) {
      const current = options(counts[index], repeat * counts.length + index + 1);
      const sample = await updateDashboard(charts, current, settings);
      dashboard.push(sample);
      // ECharts renders all four options in one task. Keep the heatmap's own
      // cost visible with a one-chart sample taken after the dashboard paint.
      heatmap.push(await update(charts[0], current[0], settings));
    }
  }

  const dataZoom = charts[1].getOption().dataZoom as Array<{ start?: number; end?: number }> | undefined;
  const zoom = dataZoom?.[0];
  const report = {
    heatmapPaintedMs: summary(heatmap.slice(counts.length).map(({ paintedMs }) => paintedMs)),
    dashboardPaintedMs: summary(dashboard.slice(counts.length).map(({ paintedMs }) => paintedMs)),
    dashboardSynchronousMs: summary(dashboard.slice(counts.length).map(({ synchronousMs }) => synchronousMs)),
    zoomAfterUpdate: [Number(zoom?.start ?? 0), Number(zoom?.end ?? 100)] as [number, number],
  };
  charts.forEach((chart) => chart.dispose());
  document.querySelector('#charts')!.replaceChildren();
  console.info(`${name}:`, report);
  return report;
}

const report: BenchmarkReport = {
  environment: { userAgent: navigator.userAgent, devicePixelRatio: window.devicePixelRatio, chartSize: '480x300 @ 2x', publicationBudgetMs: 250 },
  workload: { snapshots: 39, maximumHeatmapCells: 8_687, lineCharts: 3 },
  strategies: {},
};

report.strategies.wholesale = await runStrategy('wholesale', { notMerge: true, lazyUpdate: true });
report.strategies.merge = await runStrategy('merge', { lazyUpdate: true });
report.strategies.replaceSeries = await runStrategy('replaceSeries', { replaceMerge: ['series'], lazyUpdate: true });
window.__ECHARTS_BENCHMARK__ = report;
document.querySelector('#result')!.textContent = JSON.stringify(report, null, 2);
