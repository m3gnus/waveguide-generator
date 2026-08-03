import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import type { EChartsOption } from 'echarts';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection, fetchJobResults, type JobResults } from '../api/results';
import { EChart, useChartTokens, type ChartTokens } from '../results/EChart';
import { directivityGrid, impedanceSeries, nearestFrequencyIndex, polarSeries, splSeries, type NamedResult } from '../results/mappers';

function frequency(value: number | undefined): string {
  if (!value) return '—';
  return value >= 1_000 ? `${(value / 1_000).toFixed(value >= 10_000 ? 1 : 2)} kHz` : `${Math.round(value)} Hz`;
}

function labelFor(id: string, jobs: ReturnType<typeof jobsSocket.getSnapshot>['jobs']): string {
  const job = jobs.find((item) => item.id === id);
  return job?.label || `${String(job?.config_summary.formula_type ?? 'job').toLowerCase()} ${id.slice(0, 6)}`;
}

function axes(tokens: ChartTokens) {
  return {
    axisLine: { lineStyle: { color: tokens.grid } },
    axisLabel: { color: tokens.muted, fontSize: 9 },
    splitLine: { lineStyle: { color: tokens.grid } },
    minorSplitLine: { lineStyle: { color: tokens.gridMinor } },
  };
}

function splOption(items: NamedResult[], tokens: ChartTokens): EChartsOption {
  return {
    animation: false, color: tokens.series,
    tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 4, textStyle: { color: tokens.muted, fontSize: 9 } },
    grid: { left: 42, right: 12, top: 25, bottom: 27 },
    xAxis: { type: 'log', min: 200, max: 20_000, name: 'Hz', nameTextStyle: { color: tokens.muted }, ...axes(tokens) },
    yAxis: { type: 'value', name: 'dB SPL', nameTextStyle: { color: tokens.muted }, ...axes(tokens) },
    series: splSeries(items).map((series, index) => ({ ...series, lineStyle: { width: index ? 1.2 : 2, type: index ? 'dashed' : 'solid' } })),
  };
}

function heatmapOption(result: JobResults, tokens: ChartTokens): EChartsOption {
  const grid = directivityGrid(result, 'horizontal');
  return {
    animation: false,
    tooltip: { trigger: 'item' },
    grid: { left: 42, right: 42, top: 12, bottom: 27 },
    xAxis: { type: 'log', min: 200, max: 20_000, ...axes(tokens) },
    yAxis: { type: 'value', min: grid.angles[0] ?? -90, max: grid.angles.at(-1) ?? 90, name: '°', nameTextStyle: { color: tokens.muted }, ...axes(tokens) },
    visualMap: { min: grid.minDb, max: 0, right: 0, top: 'middle', itemWidth: 8, itemHeight: 70, textStyle: { color: tokens.muted, fontSize: 8 }, inRange: { color: tokens.colormap } },
    series: [{ type: 'heatmap', progressive: 0, data: grid.data.filter((cell) => cell[2] !== null), emphasis: { disabled: true } }],
  };
}

function polarOption(result: JobResults, index: number, plane: 'horizontal' | 'vertical', tokens: ChartTokens): EChartsOption {
  return {
    animation: false, color: [tokens.accent],
    tooltip: { trigger: 'item' },
    polar: { radius: '72%' },
    angleAxis: { type: 'value', min: -180, max: 180, startAngle: 90, axisLabel: { color: tokens.muted, fontSize: 8 }, splitLine: { lineStyle: { color: tokens.grid } } },
    radiusAxis: { min: -40, max: 0, axisLabel: { color: tokens.muted, fontSize: 8 }, splitLine: { lineStyle: { color: tokens.grid } } },
    series: [{ type: 'line', coordinateSystem: 'polar', showSymbol: false, areaStyle: { opacity: .1 }, data: polarSeries(result, index, plane) }],
  };
}

function impedanceOption(result: JobResults, mode: 'cartesian' | 'polar', tokens: ChartTokens): EChartsOption {
  return {
    animation: false, color: [tokens.series[0], tokens.series[1]], tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 4, textStyle: { color: tokens.muted, fontSize: 9 } },
    grid: { left: 42, right: mode === 'polar' ? 42 : 12, top: 25, bottom: 27 },
    xAxis: { type: 'log', min: 200, max: 20_000, ...axes(tokens) },
    yAxis: [
      { type: 'value', name: mode === 'polar' ? '|Z|' : 'Z/ρc', nameTextStyle: { color: tokens.muted }, ...axes(tokens) },
      ...(mode === 'polar' ? [{ type: 'value' as const, name: '°', nameTextStyle: { color: tokens.muted }, axisLabel: { color: tokens.muted, fontSize: 9 }, splitLine: { show: false } }] : []),
    ],
    series: impedanceSeries(result, mode),
  };
}

function ChartCard({ className, title, subtitle, children, style }: { className: string; title: string; subtitle: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return <section className={`result-card ${className}`} style={style}><header><b>{title}</b><span>{subtitle}</span></header><div className="chart-placeholder">{children}</div></section>;
}

export function ResultsPanel() {
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const tokens = useChartTokens();
  const [loaded, setLoaded] = useState<Record<string, JobResults>>({});
  const [error, setError] = useState<string | null>(null);
  const [frequencyIndex, setFrequencyIndex] = useState(0);
  const [polarPlane, setPolarPlane] = useState<'horizontal' | 'vertical'>('horizontal');
  const [impedanceMode, setImpedanceMode] = useState<'cartesian' | 'polar'>('cartesian');

  useEffect(() => {
    if (selection.primary && jobs.some((job) => job.id === selection.primary && job.has_results)) return;
    const latest = jobs.find((job) => job.status === 'complete' && job.has_results);
    if (latest) compareSelection.setPrimary(latest.id);
  }, [jobs, selection.primary]);

  const ids = useMemo(() => [selection.primary, ...selection.overlays].filter((id): id is string => Boolean(id)), [selection]);
  useEffect(() => {
    let live = true;
    if (!ids.length) { setLoaded({}); return; }
    setError(null);
    void Promise.all(ids.map(async (id) => [id, await fetchJobResults(id)] as const)).then((pairs) => {
      if (live) setLoaded(Object.fromEntries(pairs));
    }).catch((reason) => live && setError(reason instanceof Error ? reason.message : String(reason)));
    return () => { live = false; };
  }, [ids.join('|')]);

  const primary = selection.primary ? loaded[selection.primary] : undefined;
  useEffect(() => {
    if (primary?.frequencies.length) setFrequencyIndex(nearestFrequencyIndex(primary.frequencies, 1_000));
  }, [selection.primary, primary]);

  const named = ids.flatMap((id) => loaded[id] ? [{ id, label: labelFor(id, jobs), result: loaded[id] }] : []);
  const available = jobs.filter((job) => job.status === 'complete' && job.has_results && !ids.includes(job.id));
  const selectedFrequency = primary?.frequencies[frequencyIndex];

  if (!selection.primary && !jobs.some((job) => job.has_results)) {
    return <div className="results-panel panel-scroll"><div className="coming-soon" role="status"><b>NO RESULTS</b><span>Run a solve to populate frequency response, directivity, polar, and impedance charts.</span></div></div>;
  }

  return <div className="results-panel panel-scroll">
    <div className="results-toolbar">
      {ids.map((id, index) => <button key={id} className={`result-chip ${index ? 'muted' : ''}`} onClick={() => compareSelection.remove(id)} title="Remove from comparison"><i/>{labelFor(id, jobs)} ×</button>)}
      <select aria-label="Add comparison result" value="" onChange={(event) => { if (event.target.value) compareSelection.toggleOverlay(event.target.value); }} style={{ color: 'var(--fg2)', background: 'var(--ctl-grad)', border: '1px dashed var(--hair)', borderRadius: 10, fontSize: 10 }}>
        <option value="">+ compare</option>{available.map((job) => <option key={job.id} value={job.id}>{labelFor(job.id, jobs)}</option>)}
      </select>
      <span className="spacer"/><span>cursor</span><b>{frequency(selectedFrequency)}</b>
    </div>
    {error && <div className="job-error" role="alert" style={{ margin: 7 }}>{error}</div>}
    {!primary ? <div className="coming-soon"><b>LOADING RESULTS</b><span>Fetching selected job data…</span></div> : <div className="result-grid">
      <ChartCard className="result-0" title="SPL / FR" subtitle="normalized · 1 m"><EChart option={splOption(named, tokens)} label="Multi-job sound pressure frequency response"/></ChartCard>
      <ChartCard className="result-1" title="Directivity" subtitle="horizontal · dB"><EChart option={heatmapOption(primary, tokens)} label="Horizontal directivity heatmap by angle and frequency"/></ChartCard>
      <ChartCard className="result-2" title="Polar" subtitle={frequency(selectedFrequency)}>
        <div style={{ position: 'absolute', zIndex: 2, top: 1, right: 2, display: 'flex', gap: 3 }} className="segments"><button className={polarPlane === 'horizontal' ? 'on' : ''} onClick={() => setPolarPlane('horizontal')}>H</button><button className={polarPlane === 'vertical' ? 'on' : ''} onClick={() => setPolarPlane('vertical')}>V</button></div>
        <EChart option={polarOption(primary, frequencyIndex, polarPlane, tokens)} label={`${polarPlane} polar response at ${frequency(selectedFrequency)}`}/>
        <input aria-label="Polar frequency" type="range" min={0} max={Math.max(0, primary.frequencies.length - 1)} value={frequencyIndex} onChange={(event) => setFrequencyIndex(Number(event.target.value))} style={{ position: 'absolute', right: 8, bottom: 2, left: 8, width: 'calc(100% - 16px)' }}/>
      </ChartCard>
      <ChartCard className="result-3" title="Impedance" subtitle={impedanceMode === 'cartesian' ? 'Re / Im · Z/ρc' : 'magnitude / phase'} style={{ gridColumn: '5 / 13' }}>
        <div style={{ position: 'absolute', zIndex: 2, top: 1, right: 2 }} className="segments"><button className={impedanceMode === 'cartesian' ? 'on' : ''} onClick={() => setImpedanceMode('cartesian')}>Re/Im</button><button className={impedanceMode === 'polar' ? 'on' : ''} onClick={() => setImpedanceMode('polar')}>|Z|/φ</button></div>
        <EChart option={impedanceOption(primary, impedanceMode, tokens)} label="Normalized acoustic impedance by frequency"/>
      </ChartCard>
    </div>}
  </div>;
}
