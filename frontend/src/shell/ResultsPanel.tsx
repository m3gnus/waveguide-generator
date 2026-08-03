import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import type { EChartsOption } from 'echarts';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection, fetchJobResults, type JobResults } from '../api/results';
import { useDesignStore } from '../stores/design';
import { EChart, useChartTokens, type ChartTokens } from '../results/EChart';
import { beamShapeSeries, directivityGrid, directivityIndexSeries, impedanceSeries, splSeries, type NamedResult } from '../results/mappers';
import { BalloonRenderer, ChartStub, ForwardBeamRenderer } from '../results/balloon';
import { runExportBundle } from '../results/exporters';
import type { ResultPayload } from '../results/types';
import { CHART_TYPES, preferencesStore, usePreferences, type ChartType } from '../prefs/preferences';
import { ResultsPreferencesSurface } from '../prefs/PreferencesSurface';

function frequency(value: number | undefined): string {
  if (!value) return '—';
  return value >= 1_000 ? `${(value / 1_000).toFixed(value >= 10_000 ? 1 : 2)} kHz` : `${Math.round(value)} Hz`;
}

export function splSubtitle(result: JobResults | undefined): string {
  const observation = result?.metadata?.observation;
  const record = observation && typeof observation === 'object' ? observation as Record<string, unknown> : {};
  const distance = Number(record.effective_distance_m ?? record.requested_distance_m);
  return Number.isFinite(distance) && distance > 0 ? `absolute · ${Number(distance.toPrecision(4))} m` : 'absolute · distance unspecified';
}

function labelFor(id: string, jobs: ReturnType<typeof jobsSocket.getSnapshot>['jobs']): string {
  const job = jobs.find((item) => item.id === id);
  return job?.label || `${String(job?.config_summary.formula_type ?? 'job').toLowerCase()} ${id.slice(0, 6)}`;
}

function axes(tokens: ChartTokens) {
  return { axisLine: { lineStyle: { color: tokens.grid } }, axisLabel: { color: tokens.muted, fontSize: 9 }, splitLine: { lineStyle: { color: tokens.grid } }, minorSplitLine: { lineStyle: { color: tokens.gridMinor } } };
}

function lineOption(series: EChartsOption['series'], tokens: ChartTokens, yName: string): EChartsOption {
  return { animation: false, color: tokens.series, tooltip: { trigger: 'axis' }, legend: { top: 0, right: 4, textStyle: { color: tokens.muted, fontSize: 9 } }, grid: { left: 42, right: 12, top: 25, bottom: 27 }, xAxis: { type: 'log', name: 'Hz', nameTextStyle: { color: tokens.muted }, ...axes(tokens) }, yAxis: { type: 'value', name: yName, nameTextStyle: { color: tokens.muted }, ...axes(tokens) }, series };
}

function splOption(items: NamedResult[], tokens: ChartTokens, smoothing: ReturnType<typeof usePreferences>['smoothing']): EChartsOption {
  return lineOption(splSeries(items, smoothing).map((series, index) => ({ ...series, lineStyle: { width: index ? 1.2 : 2, type: index ? 'dashed' : 'solid' } })), tokens, 'dB SPL');
}

function heatmapOption(result: ResultPayload, tokens: ChartTokens, plane: string, mapReference: number): EChartsOption {
  const grid = directivityGrid(result, plane);
  return { animation: false, tooltip: { trigger: 'item' }, grid: { left: 42, right: 42, top: 12, bottom: 27 }, xAxis: { type: 'log', ...axes(tokens) }, yAxis: { type: 'value', min: grid.angles[0] ?? -90, max: grid.angles.at(-1) ?? 90, name: '°', nameTextStyle: { color: tokens.muted }, ...axes(tokens) }, visualMap: { min: mapReference * 5, max: 0, right: 0, top: 'middle', itemWidth: 8, itemHeight: 70, text: ['0', `${mapReference} ref`], textStyle: { color: tokens.muted, fontSize: 8 }, inRange: { color: tokens.colormap } }, series: [{ type: 'heatmap', progressive: 0, data: grid.data.filter((cell) => cell[2] !== null), emphasis: { disabled: true } }] };
}

function impedanceOption(result: ResultPayload, tokens: ChartTokens, smoothing: ReturnType<typeof usePreferences>['smoothing']): EChartsOption {
  return { ...lineOption(impedanceSeries(result, 'cartesian', smoothing), tokens, 'Z/ρc'), color: [tokens.series[0], tokens.series[1]] };
}

function chartLabel(chartType: ChartType): string {
  return CHART_TYPES.find(({ id }) => id === chartType)?.label ?? chartType;
}

function Summary({ result }: { result: ResultPayload }) {
  const warnings = Array.isArray(result.metadata?.warnings) ? result.metadata.warnings.length : Number(result.metadata?.warning_count ?? 0);
  const cells = [
    ['Frequencies', result.frequencies.length],
    ['Range', result.frequencies.length ? `${frequency(result.frequencies[0])} – ${frequency(result.frequencies.at(-1))}` : '—'],
    ['Directivity planes', Object.keys(result.directivity ?? {}).join(', ') || 'none'],
    ['Balloon samples', result.balloon?.spl_norm_db.length ?? 0],
    ['Warnings', warnings],
    ['Contract', String(result.metadata?.result_contract_version ?? 'legacy')],
  ];
  return <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, margin: 10 }}>{cells.map(([label, value]) => <div key={label}><dt style={{ color: 'var(--fg3)', fontSize: 9 }}>{label}</dt><dd style={{ margin: 0, color: 'var(--fg1)', font: '11px var(--mono)' }}>{value}</dd></div>)}</dl>;
}

function ResultChart({ chartType, result, named, tokens }: { chartType: ChartType; result: ResultPayload; named: NamedResult[]; tokens: ChartTokens }) {
  const preferences = usePreferences();
  if (chartType === 'frequency_response') return result.spl_on_axis?.spl?.length ? <EChart option={splOption(named, tokens, preferences.smoothing)} label="Multi-job sound pressure frequency response"/> : <ChartStub reason="Frequency Response needs spl_on_axis data from a completed solve."/>;
  if (chartType === 'directivity_map_h' || chartType === 'directivity_map_v') {
    const plane = chartType.endsWith('_v') ? 'vertical' : 'horizontal';
    return result.directivity?.[plane]?.length ? <EChart option={heatmapOption(result, tokens, plane, preferences.mapReference)} label={`${plane} directivity heatmap`}/> : <ChartStub reason={`Directivity Map (${plane === 'horizontal' ? 'H' : 'V'}) needs the ${plane} polar plane in the result payload.`}/>;
  }
  if (chartType === 'directivity_map') {
    const directivity = result.directivity as Record<string, unknown[]> | undefined;
    const planes = Object.keys(directivity ?? {}).filter((plane) => directivity?.[plane]?.length);
    return planes.length ? <div style={{ display: 'flex', width: '100%', height: '100%' }}>{planes.map((plane) => <div key={plane} style={{ position: 'relative', flex: 1 }}><EChart option={heatmapOption(result, tokens, plane, preferences.mapReference)} label={`${plane} directivity heatmap`}/></div>)}</div> : <ChartStub reason="Directivity Map needs at least one polar plane in the result payload."/>;
  }
  if (chartType === 'directivity_index') {
    const series = directivityIndexSeries(result, preferences.smoothing);
    return series.length ? <EChart option={lineOption(series, tokens, 'DI dB')} label="Directivity index by frequency"/> : <ChartStub reason="Directivity Index needs the optional di result block."/>;
  }
  if (chartType === 'beam_shape') return result.beam_shape?.frequencies?.length ? <EChart option={lineOption(beamShapeSeries(result), tokens, 'degrees')} label="Horizontal and vertical forward beam width"/> : <ChartStub reason="Forward Beam Shape needs spherical balloon sampling and a valid −6 dB contour fit."/>;
  if (chartType === 'beam_map') return <ForwardBeamRenderer result={result}/>;
  if (chartType === 'balloon') return <BalloonRenderer result={result}/>;
  if (chartType === 'impedance') return result.impedance?.frequencies?.length ? <EChart option={impedanceOption(result, tokens, preferences.smoothing)} label="Normalized acoustic impedance by frequency"/> : <ChartStub reason="Acoustic Impedance needs the optional impedance result block."/>;
  return <Summary result={result}/>;
}

function ChartCard({ index, chartType, result, named, tokens }: { index: number; chartType: ChartType; result: ResultPayload; named: NamedResult[]; tokens: ChartTokens }) {
  return <section className={`result-card result-${index}`} style={{ gridColumn: index % 2 ? '7 / 13' : '1 / 7' }}>
    <header style={{ alignItems: 'center' }}><select aria-label={`Panel ${index + 1} chart type`} value={chartType} onChange={(event) => preferencesStore.setChartType(index, event.target.value as ChartType)} style={{ maxWidth: '72%', color: 'var(--fg1)', background: 'var(--ctl-grad)', border: '1px solid var(--hair)', fontSize: 9 }}>{CHART_TYPES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}</select><span>{chartType.startsWith('directivity_map') ? `ref ${preferencesStore.getSnapshot().mapReference} dB` : chartType === 'frequency_response' ? splSubtitle(result) : chartLabel(chartType)}</span></header>
    <div className="chart-placeholder"><ResultChart chartType={chartType} result={result} named={named} tokens={tokens}/></div>
  </section>;
}

export function ResultsPanel() {
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const preferences = usePreferences();
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const tokens = useChartTokens();
  const [loaded, setLoaded] = useState<Record<string, ResultPayload>>({});
  const [error, setError] = useState<string | null>(null);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (selection.primary && jobs.some((job) => job.id === selection.primary && job.has_results)) return;
    const latest = jobs.find((job) => job.status === 'complete' && job.has_results);
    if (latest) compareSelection.setPrimary(latest.id); else if (selection.primary) compareSelection.setPrimary(null);
  }, [jobs, selection.primary]);

  const ids = useMemo(() => [selection.primary, ...selection.overlays].filter((id): id is string => Boolean(id)), [selection]);
  useEffect(() => {
    let live = true;
    if (!ids.length) { setLoaded({}); return; }
    setError(null);
    void Promise.all(ids.map(async (id) => [id, await fetchJobResults(id) as ResultPayload] as const)).then((pairs) => { if (live) setLoaded(Object.fromEntries(pairs)); }).catch((reason) => live && setError(reason instanceof Error ? reason.message : String(reason)));
    return () => { live = false; };
  }, [ids.join('|')]);

  const primary = selection.primary ? loaded[selection.primary] : undefined;
  const named = ids.flatMap((id) => loaded[id] ? [{ id, label: labelFor(id, jobs), result: loaded[id] }] : []);
  const available = jobs.filter((job) => job.status === 'complete' && job.has_results && !ids.includes(job.id));
  const exportSelected = async () => {
    if (!primary) return;
    setExporting(true); setExportStatus(null);
    try {
      const result = await runExportBundle({ result: primary, design, designRevision, preferences });
      if (selection.primary && result.files.length) {
        const job = jobs.find(({ id }) => id === selection.primary);
        await jobsSocket.patchMetadata(selection.primary, { exported_files: [...new Set([...(job?.exported_files ?? []), ...result.files])] });
      }
      if (result.files.length) preferencesStore.update({ counter: Math.min(999_999, preferences.counter + 1) });
      setExportStatus(`${result.files.length} file${result.files.length === 1 ? '' : 's'} exported${result.failures.length ? ` · ${result.failures.length} failed: ${result.failures.map(({ format, reason }) => `${format} (${reason})`).join(', ')}` : ''}`);
    } catch (reason) { setExportStatus(reason instanceof Error ? reason.message : String(reason)); }
    finally { setExporting(false); }
  };

  if (!selection.primary && !jobs.some((job) => job.has_results)) return <div className="results-panel panel-scroll"><ResultsPreferencesSurface/><div className="coming-soon" role="status"><b>NO RESULTS</b><span>Run a solve to populate result charts.</span></div></div>;

  return <div className="results-panel panel-scroll">
    <div className="results-toolbar">
      {ids.map((id, index) => <button key={id} className={`result-chip ${index ? 'muted' : ''}`} onClick={() => compareSelection.remove(id)} title="Remove from comparison"><i/>{labelFor(id, jobs)} ×</button>)}
      <select aria-label="Add comparison result" value="" onChange={(event) => { if (event.target.value) compareSelection.toggleOverlay(event.target.value); }} style={{ color: 'var(--fg2)', background: 'var(--ctl-grad)', border: '1px dashed var(--hair)', borderRadius: 10, fontSize: 10 }}><option value="">+ compare</option>{available.map((job) => <option key={job.id} value={job.id}>{labelFor(job.id, jobs)}</option>)}</select>
      <span className="spacer"/><button disabled={exporting || !primary || !preferences.exportFormats.length} onClick={() => void exportSelected()}>{exporting ? 'Exporting…' : `Export selected (${preferences.exportFormats.length})`}</button>
    </div>
    <ResultsPreferencesSurface/>
    {(error || exportStatus) && <div className={error ? 'job-error' : ''} role="status" style={{ margin: 7, color: error ? undefined : 'var(--fg2)', fontSize: 9 }}>{error ?? exportStatus}</div>}
    {!primary ? <div className="coming-soon"><b>LOADING RESULTS</b><span>Fetching selected job data…</span></div> : <div className="result-grid" style={{ gridTemplateRows: 'repeat(3, minmax(145px, 1fr))', minHeight: 480, flex: 'none' }}>{preferences.chartTypes.map((chartType, index) => <ChartCard key={index} index={index} chartType={chartType} result={primary} named={named} tokens={tokens}/>)}</div>}
  </div>;
}
