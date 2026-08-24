import { buildDerivedAcoustics, type DerivedAcousticsRow } from './derivedAcoustics';
import { groupDelayValue } from './mappers';
import { radiatedPowerMetadata } from './radiatedPower';
import { resultChannels, type ResultPayload } from './types';
import type { GroupDelayUnit } from '../prefs/preferences';

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
}

interface PlotSeries {
  key: keyof DerivedAcousticsRow;
  label: string;
  color: string;
}

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

function svgLineChart(
  rows: DerivedAcousticsRow[],
  series: PlotSeries[],
  yLabel: string,
): string {
  const width = 820;
  const height = 260;
  const margin = { left: 58, right: 18, top: 18, bottom: 42 };
  const frequencies = rows.map(({ frequency_hz }) => frequency_hz).filter((value) => value > 0);
  const values = series.flatMap(({ key }) => rows.map((row) => row[key]).filter(finite));
  if (frequencies.length < 2 || !values.length) return '<p class="empty">No samples available for this plot.</p>';
  const xMin = Math.log10(Math.min(...frequencies));
  const xMax = Math.log10(Math.max(...frequencies));
  let yMin = Math.min(...values);
  let yMax = Math.max(...values);
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const pad = (yMax - yMin) * 0.08;
  yMin -= pad;
  yMax += pad;
  const x = (frequency: number) => margin.left
    + ((Math.log10(frequency) - xMin) / (xMax - xMin)) * (width - margin.left - margin.right);
  const y = (value: number) => margin.top
    + ((yMax - value) / (yMax - yMin)) * (height - margin.top - margin.bottom);
  const paths = series.map(({ key, label, color }) => {
    const segments: string[][] = [];
    let current: string[] = [];
    rows.forEach((row) => {
      const value = row[key];
      if (!finite(value) || !(row.frequency_hz > 0)) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      current.push(`${x(row.frequency_hz).toFixed(2)},${y(value).toFixed(2)}`);
    });
    if (current.length) segments.push(current);
    return `${segments.map((points) => `<polyline fill="none" stroke="${color}" stroke-width="2" points="${points.join(' ')}"/>`).join('')}
      <g class="legend" transform="translate(${margin.left + series.indexOf(series.find((item) => item.key === key)!) * 175},${height - 10})">
        <line x1="0" y1="0" x2="24" y2="0" stroke="${color}" stroke-width="3"/><text x="31" y="4">${escapeHtml(label)}</text>
      </g>`;
  }).join('');
  const xStart = Math.min(...frequencies);
  const xEnd = Math.max(...frequencies);
  return `<svg class="plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(yLabel)} by frequency">
    <rect width="${width}" height="${height}" fill="#10151d" rx="8"/>
    <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="#52606f"/>
    <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="#52606f"/>
    <text x="${margin.left}" y="${height - margin.bottom + 20}" text-anchor="middle">${escapeHtml(format(xStart, 0))} Hz</text>
    <text x="${width - margin.right}" y="${height - margin.bottom + 20}" text-anchor="end">${escapeHtml(format(xEnd, 0))} Hz</text>
    <text x="8" y="14">${escapeHtml(format(yMax, 1))}</text>
    <text x="8" y="${height - margin.bottom}">${escapeHtml(format(yMin, 1))}</text>
    ${paths}
  </svg>`;
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

function channelSection(id: string, result: ResultPayload, groupDelayUnit: GroupDelayUnit): string {
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
    <h2>${escapeHtml(id)}</h2>
    <div class="cards">
      <div><span>Frequency range</span><strong>${frequencies.length ? `${format(Math.min(...frequencies), 0)}–${format(Math.max(...frequencies), 0)} Hz` : '—'}</strong></div>
      <div><span>Samples</span><strong>${rows.length}</strong></div>
      <div><span>Mean on-axis SPL</span><strong>${format(mean(rows.map((row) => row.on_axis_spl_db)))} dB</strong></div>
      <div><span>Mean DI</span><strong>${format(mean(rows.map((row) => row.directivity_index_db)))} dB</strong></div>
      ${radiatedPower ? `<div><span>Max |power agreement|</span><strong>${format(maximumAbsolute(rows.map((row) => row.power_agreement_db)), 3)} dB</strong></div>` : ''}
    </div>
    <h3>Response and power response</h3>
    ${svgLineChart(rows, [
      { key: 'on_axis_spl_db', label: 'On-axis SPL', color: '#5bd4ff' },
      { key: 'power_response_db_spl_avg', label: 'Power response', color: '#ffbf69' },
    ], 'Level (dB)')}
    <h3>-6 dB beamwidth</h3>
    ${svgLineChart(rows, [
      { key: 'horizontal_beamwidth_deg', label: 'Horizontal', color: '#78e08f' },
      { key: 'vertical_beamwidth_deg', label: 'Vertical', color: '#c792ea' },
    ], 'Beamwidth (degrees)')}
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

/** A single-file report: inline CSS/SVG only, with no network dependencies. */
export function buildRunReportHtml(result: ResultPayload, options: RunReportOptions): string {
  const channels = resultChannels(result);
  const groupDelayUnit = options.groupDelayUnit ?? 'ms';
  const sections = channels.length
    ? channels.map(({ id, result: channel }) => channelSection(id, channel, groupDelayUnit)).join('')
    : channelSection('Result', result, groupDelayUnit);
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>${escapeHtml(options.title)} · HornLab run report</title>
<style>
:root{color-scheme:dark;font:14px/1.5 ui-sans-serif,system-ui,sans-serif;background:#0b0f14;color:#e7edf4}body{max-width:1100px;margin:auto;padding:40px 24px}header{border-bottom:1px solid #29323d;margin-bottom:30px}h1{font-size:30px;margin-bottom:4px}h2{font-size:23px;margin-top:36px}h3{color:#bac7d5;margin-top:24px}.quiet,time,summary{color:#91a0af}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.cards div{background:#151c25;border:1px solid #29323d;border-radius:8px;padding:12px}.cards span{display:block;color:#91a0af}.cards strong{font-size:18px}.plot{width:100%;height:auto}.plot text{fill:#91a0af;font-size:11px}.legend text{fill:#dbe6f1}.warnings{border-left:3px solid #ffbf69;padding-left:24px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;margin-top:12px}th,td{text-align:right;border-bottom:1px solid #29323d;padding:7px}th{position:sticky;top:0;background:#151c25}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#10151d;padding:14px;border-radius:8px}details{margin-top:16px}summary{cursor:pointer}.empty{padding:40px;background:#10151d;border-radius:8px;text-align:center}
</style></head><body><header><h1>${escapeHtml(options.title)}</h1><p>HornLab static run report · generated <time datetime="${escapeHtml(options.generatedAt.toISOString())}">${escapeHtml(options.generatedAt.toISOString())}</time></p></header>${sections}</body></html>\n`;
}
