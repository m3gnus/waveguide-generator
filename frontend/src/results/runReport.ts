import { samplingNotes } from './sampling';
import { buildDerivedAcoustics, type DerivedAcousticsRow } from './derivedAcoustics';
import { groupDelayValue } from './mappers';
import { radiatedPowerMetadata } from './radiatedPower';
import { resultChannels, type ResultPayload } from './types';
import type { GroupDelayUnit } from '../prefs/preferences';
import { activityOperation } from '../shell/activityOperation';
import { renderChartSpec } from './chartImage';
import type { LineChartSpec } from './chartSpec';

interface RunReportOptions {
  title: string;
  generatedAt: Date;
  /**
   * The unit the group delay column is written in, following the same
   * preference the chart reads. Defaults to milliseconds so a caller that has
   * no preferences to hand still writes the unit the header names. The derived
   * acoustics CSV and JSON are unaffected: `group_delay_ms` is a data contract
   * and stays in milliseconds whatever the report is read in.
   */
  groupDelayUnit?: GroupDelayUnit;
  /**
   * The chart images, from `renderRunReportCharts`. A chart missing here is
   * reported as having no samples, so the report still opens.
   */
  charts?: RunReportCharts;
}

/** The report's chart images by section, as PNG data URIs from hornlab-plots. */
export type RunReportCharts = Record<string, Partial<Record<ReportChart, string>>>;

type ReportChart = 'response' | 'beamwidth';

interface ReportPlot {
  chart: ReportChart;
  heading: string;
  yLabel: string;
  series: Array<{ key: keyof DerivedAcousticsRow; label: string }>;
}

const REPORT_PLOTS: ReportPlot[] = [
  {
    chart: 'response',
    heading: 'Response and power response',
    yLabel: 'Level [dB]',
    series: [
      { key: 'on_axis_spl_db', label: 'On-axis SPL' },
      { key: 'power_response_db_spl_avg', label: 'Power response' },
    ],
  },
  {
    chart: 'beamwidth',
    heading: '-6 dB beamwidth',
    yLabel: 'Beamwidth [°]',
    series: [
      { key: 'horizontal_beamwidth_deg', label: 'Horizontal' },
      { key: 'vertical_beamwidth_deg', label: 'Vertical' },
    ],
  },
];

function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function format(value: number | null, digits = 2): string {
  return finite(value) ? value.toFixed(digits) : '—';
}

function mean(values: Array<number | null>): number | null {
  const valid = values.filter(finite);
  return valid.length
    ? valid.reduce((sum, value) => sum + value, 0) / valid.length
    : null;
}

function maximumAbsolute(values: Array<number | null>): number | null {
  const valid = values.filter(finite).map(Math.abs);
  return valid.length ? Math.max(...valid) : null;
}

/**
 * The spec of one report chart, from the derived-acoustics rows the table
 * below it lists, so the picture and the numbers are the same values. Null
 * when no series has two plottable samples.
 */
function reportPlotSpec(rows: DerivedAcousticsRow[], plot: ReportPlot): LineChartSpec | null {
  const sampled = rows.filter(({ frequency_hz }) => frequency_hz > 0);
  const series = plot.series.flatMap(({ key, label }, slot) => {
    const values = sampled.map((row) => (finite(row[key]) ? row[key] as number : null));
    return values.filter(finite).length ? [{
      label,
      x: sampled.map(({ frequency_hz }) => frequency_hz),
      y: values,
      color: slot,
    }] : [];
  });
  if (sampled.length < 2 || !series.length) return null;
  return {
    kind: 'lines',
    title: plot.heading,
    x: { label: 'Frequency [Hz]', scale: 'log' },
    y: { label: plot.yLabel },
    series,
  };
}

/** Every chart the report draws, by section and chart: the same sections `buildRunReportHtml` writes. */
export function runReportChartSpecs(result: ResultPayload): Array<{ section: string; chart: ReportChart; spec: LineChartSpec }> {
  return reportSections(result).flatMap(({ id, result: channel }) => {
    const rows = buildDerivedAcoustics(channel).rows;
    return REPORT_PLOTS.flatMap((plot) => {
      const spec = reportPlotSpec(rows, plot);
      return spec ? [{ section: id, chart: plot.chart, spec }] : [];
    });
  });
}

/**
 * Draw the report's charts with hornlab-plots in `theme`. One request per chart,
 * counted on the status bar, since a multi-channel report draws several.
 */
export async function renderRunReportCharts(result: ResultPayload, theme: string, fetcher: typeof fetch): Promise<RunReportCharts> {
  const specs = runReportChartSpecs(result);
  return activityOperation({ kind: 'export', label: 'Drawing report charts' }, fetcher, async (scoped, activity) => {
    // Inside an export this joins the export's activity, which keeps its own
    // label unless told: say what the counted steps are, then hand it back.
    const outer = activity.currentLabel();
    activity.label('Drawing report charts');
    try {
      const charts: RunReportCharts = {};
      activity.progress(0, specs.length);
      for (const [index, { section, chart, spec }] of specs.entries()) {
        (charts[section] ??= {})[chart] = await renderChartSpec(spec, theme, scoped, 'WG could not draw the charts for this report. Try the export again.');
        activity.progress(index + 1, specs.length);
      }
      return charts;
    } finally {
      activity.label(outer);
    }
  });
}

function plotImage(image: string | undefined, heading: string, sampled: boolean): string {
  if (image) return `<img class="plot" alt="${escapeHtml(heading)} by frequency" src="${escapeHtml(image)}">`;
  return sampled
    ? '<p class="empty">This chart was not drawn for this report. Export the report again to include it.</p>'
    : '<p class="empty">No samples available for this plot.</p>';
}

function warningList(result: ResultPayload): string {
  const warnings = [
    ...(Array.isArray(result.metadata?.warnings) ? result.metadata.warnings : []),
    ...(Array.isArray(result.metadata?.combine?.warnings) ? result.metadata.combine.warnings : []),
  ].map(String);
  return warnings.length
    ? `<ul class="warnings">${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}</ul>`
    : '<p class="quiet">No result warnings.</p>';
}

function channelSection(id: string, result: ResultPayload, groupDelayUnit: GroupDelayUnit, charts: Partial<Record<ReportChart, string>>): string {
  const derived = buildDerivedAcoustics(result);
  const radiatedPower = radiatedPowerMetadata(result);
  const rows = derived.rows;
  const frequencies = rows.map(({ frequency_hz }) => frequency_hz);
  const table = rows.map((row) => `<tr>
      <td>${format(row.frequency_hz, 2)}</td><td>${format(row.on_axis_spl_db)}</td>
      <td>${format(row.directivity_index_db)}</td><td>${format(row.power_response_db_spl_avg)}</td>
      <td>${format(row.radiated_power_surface_w, 6)}</td><td>${format(row.radiated_power_sphere_w, 6)}</td>
      <td>${format(row.power_agreement_db, 3)}</td>
      <td>${format(row.group_delay_ms === null ? null : groupDelayValue(row.group_delay_ms, row.frequency_hz, groupDelayUnit), 4)}</td><td>${format(row.horizontal_beamwidth_deg)}</td>
      <td>${format(row.vertical_beamwidth_deg)}</td>
    </tr>`).join('');
  return `<section>
    <h2>${escapeHtml(id)}</h2>${samplingNotes(result).map((note) => `<p class="warnings">${escapeHtml(note)}</p>`).join('')}
    <div class="cards">
      <div><span>Frequency range</span><strong>${frequencies.length ? `${format(Math.min(...frequencies), 0)}–${format(Math.max(...frequencies), 0)} Hz` : '—'}</strong></div>
      <div><span>Samples</span><strong>${rows.length}</strong></div>
      <div><span>Mean on-axis SPL</span><strong>${format(mean(rows.map((row) => row.on_axis_spl_db)))} dB</strong></div>
      <div><span>Mean DI</span><strong>${format(mean(rows.map((row) => row.directivity_index_db)))} dB</strong></div>
      ${radiatedPower ? `<div><span>Max |power agreement|</span><strong>${format(maximumAbsolute(rows.map((row) => row.power_agreement_db)), 3)} dB</strong></div>` : ''}
    </div>
    ${REPORT_PLOTS.map((plot) => `<h3>${escapeHtml(plot.heading)}</h3>
    ${plotImage(charts[plot.chart], plot.heading, reportPlotSpec(rows, plot) !== null)}`).join('\n    ')}
    <h3>Warnings</h3>${warningList(result)}
    ${radiatedPower ? `<details><summary>Radiated-power cross-check</summary>
      <p class="quiet">${escapeHtml(radiatedPower.definition)}</p>
      <pre>${escapeHtml(JSON.stringify(radiatedPower, null, 2))}</pre>
    </details>` : ''}
    <details><summary>Derived acoustics table (${rows.length} rows)</summary>
      <div class="table-wrap"><table><thead><tr><th>Hz</th><th>SPL dB</th><th>DI dB</th><th>Power response dB</th><th>Surface power W</th><th>Sphere power W</th><th>Agreement dB</th><th>Group delay ${escapeHtml(groupDelayUnit)}</th><th>H beamwidth °</th><th>V beamwidth °</th></tr></thead><tbody>${table}</tbody></table></div>
    </details>
    <details><summary>Result metadata</summary><pre>${escapeHtml(JSON.stringify(result.metadata ?? {}, null, 2))}</pre></details>
  </section>`;
}

/** The report's sections: one per channel, or the whole result as one. */
function reportSections(result: ResultPayload): Array<{ id: string; result: ResultPayload }> {
  const channels = resultChannels(result);
  return channels.length ? channels.map(({ id, result: channel }) => ({ id, result: channel })) : [{ id: 'Result', result }];
}

/**
 * A single-file report: inline CSS and embedded PNG charts, with no network
 * dependencies, so it opens offline as one file.
 */
export function buildRunReportHtml(result: ResultPayload, options: RunReportOptions): string {
  const groupDelayUnit = options.groupDelayUnit ?? 'ms';
  const sections = reportSections(result)
    .map(({ id, result: channel }) => channelSection(id, channel, groupDelayUnit, options.charts?.[id] ?? {}))
    .join('');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>${escapeHtml(options.title)} · HornLab run report</title>
<style>
:root{color-scheme:dark;font:14px/1.5 ui-sans-serif,system-ui,sans-serif;background:#0b0f14;color:#e7edf4}body{max-width:1100px;margin:auto;padding:40px 24px}header{border-bottom:1px solid #29323d;margin-bottom:30px}h1{font-size:30px;margin-bottom:4px}h2{font-size:23px;margin-top:36px}h3{color:#bac7d5;margin-top:24px}.quiet,time,summary{color:#91a0af}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.cards div{background:#151c25;border:1px solid #29323d;border-radius:8px;padding:12px}.cards span{display:block;color:#91a0af}.cards strong{font-size:18px}.plot{display:block;width:100%;height:auto;border-radius:8px}.warnings{border-left:3px solid #ffbf69;padding-left:24px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;margin-top:12px}th,td{text-align:right;border-bottom:1px solid #29323d;padding:7px}th{position:sticky;top:0;background:#151c25}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#10151d;padding:14px;border-radius:8px}details{margin-top:16px}summary{cursor:pointer}.empty{padding:40px;background:#10151d;border-radius:8px;text-align:center}
</style></head><body><header><h1>${escapeHtml(options.title)}</h1><p>HornLab static run report · generated <time datetime="${escapeHtml(options.generatedAt.toISOString())}">${escapeHtml(options.generatedAt.toISOString())}</time></p></header>${sections}</body></html>\n`;
}
