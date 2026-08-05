import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import type { EChartsOption } from 'echarts';

const LazyEChartRenderer = lazy(async () => {
  const module = await import('./EChartRenderer');
  return { default: module.EChartRenderer };
});

export class ChartErrorBoundary extends Component<
  { children: ReactNode; label: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <div role="alert" aria-label={`${this.props.label} unavailable`} style={{ width: '100%', height: '100%', minHeight: 0 }}>
        Interactive chart unavailable. Reload to retry.
      </div>;
    }
    return this.props.children;
  }
}

export interface ChartTokens {
  background?: string;
  spine?: string;
  foreground: string;
  muted: string;
  grid: string;
  gridMinor: string;
  accent: string;
  series: string[];
  colormap: string[];
}

export function readChartTokens(): ChartTokens {
  // These values mirror hornlab_plots.style.HORNLAB_THEME. Keeping the live
  // charts on the same role palette gives them the canonical look without
  // replacing their responsive, inspectable canvas with a rendered PNG.
  const light = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light';
  return light ? {
    background: '#FFFDF8',
    foreground: '#1A1A1A',
    muted: '#5A6470',
    spine: '#CFC8BC',
    grid: 'rgba(30,34,48,.16)',
    gridMinor: 'rgba(30,34,48,.07)',
    accent: '#1F5FBF',
    series: ['#1F5FBF', '#D32222', '#2E8B3D', '#E07B10', '#8E3B7E', '#5A5A5A'],
    colormap: ['#FFFFFF', '#C8DCF0', '#7BA7D7', '#3A6FC0', '#3AA6C0', '#3EC06A', '#9ED23A', '#E6E63A', '#F0A030', '#E11414'],
  } : {
    background: '#0F1927',
    foreground: '#C8D8EC',
    muted: '#6A90B8',
    spine: '#1E3050',
    grid: 'rgba(255,255,255,.22)',
    gridMinor: 'rgba(255,255,255,.08)',
    accent: '#4FC3F7',
    series: ['#4FC3F7', '#E8A83A', '#EF5350', '#66BB6A', '#AB47BC', '#78909C'],
    colormap: ['#050C18', '#0A1E36', '#0E3058', '#1A4A7A', '#2D6090', '#4D78A8', '#3A3A7A', '#5A3090', '#7A2060', '#A83040', '#C84428'],
  };
}

export function useChartTokens(): ChartTokens {
  const [tokens, setTokens] = useState(readChartTokens);
  useEffect(() => {
    const observer = new MutationObserver(() => setTokens(readChartTokens()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return tokens;
}

export function EChart({ option, label }: { option: EChartsOption; label: string }) {
  return <ChartErrorBoundary label={label}>
    <Suspense fallback={<div role="status" aria-label={`${label} loading`} style={{ width: '100%', height: '100%', minHeight: 0 }} />}>
      <LazyEChartRenderer option={option} label={label}/>
    </Suspense>
  </ChartErrorBoundary>;
}
