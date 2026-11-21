import { SMOOTHING_MODES } from './smoothing';
import { SERIES_FIXTURES } from './series.fixtures';
import { buildFrequencyCsv, buildSummaryText } from './exporters';
import { buildDerivedAcousticsCsv, buildDerivedAcousticsJson } from './derivedAcoustics';
import { buildRunReportHtml } from './runReport';
import {
  beamFitSeries, beamShapeSeries, directivityIndexSeries, groupDelaySeries, impedanceComparable,
  impedanceSeries, phaseSeries, powerResponseSeries, splSeries,
} from './mappers';
import { drivePowerSeries } from './drivePower';
import { buildOnAxisFrd } from './frd';
import { withMeasurementAngle } from './measurementAngle';
import { buildZma } from './vituixcad';
import { groupDelayMissingReason } from '../shell/ResultsPanel';
import { preferencesStore } from '../prefs/preferences';

/**
 * Every output the series layer and its grid rules feed, on every fixture and
 * smoothing mode, as `key -> sha256(canonicalNumbers(output))`.
 *
 * Test support only. `series.capture.json` is this function's output from the
 * code before the series layer (WG dev ef17eccb1), so comparing against it
 * compares new code with old code's outputs. Only APIs that existed then are
 * called here, so the same file still runs there to recapture.
 */
export async function captureSeriesOutputs(): Promise<Record<string, string>> {
  const texts: Array<[string, string]> = [];
  const put = (key: string, produce: () => unknown) => {
    let text: string;
    try {
      const value = produce();
      text = typeof value === 'string' ? value : JSON.stringify(value) ?? 'undefined';
    } catch (error) {
      text = `throws: ${error instanceof Error ? error.message : String(error)}`;
    }
    texts.push([key, text]);
  };
  const now = new Date('2026-11-20T12:00:00Z');
  for (const [name, result] of Object.entries(SERIES_FIXTURES)) {
    const item = [{ id: 'a', label: 'A', result }];
    put(`${name}|derivedCsv`, () => buildDerivedAcousticsCsv(result));
    put(`${name}|derivedJson`, () => buildDerivedAcousticsJson(result));
    // The report's numbers: its cards and table. Its stylesheet and chart
    // blocks are left out -- the charts became hornlab-plots images (approved
    // look change, 261120) and their values are checked in runReport.test.ts.
    put(`${name}|report`, () => reportNumbers(buildRunReportHtml(result, { title: 'Run', generatedAt: now })));
    put(`${name}|phase`, () => phaseSeries(item));
    put(`${name}|groupDelay`, () => groupDelaySeries(item));
    put(`${name}|groupDelayCycles`, () => groupDelaySeries(item, 'cycles'));
    put(`${name}|groupDelayMissing`, () => groupDelayMissingReason(result));
    put(`${name}|beamShape`, () => beamShapeSeries(result));
    put(`${name}|beamFit`, () => beamFitSeries(result));
    put(`${name}|impedanceComparable`, () => impedanceComparable(item).items.map(({ id }) => id));
    put(`${name}|drivePower`, () => drivePowerSeries(result));
    put(`${name}|frd`, () => buildOnAxisFrd(result));
    put(`${name}|zma`, () => buildZma(result));
    for (const plane of ['horizontal', 'vertical'] as const) {
      for (const angle of [0, 15, -30]) {
        put(`${name}|angle|${plane}|${angle}`, () => withMeasurementAngle(result, plane, angle).spl_on_axis);
      }
    }
    for (const [mode] of SMOOTHING_MODES) {
      const preferences = { ...preferencesStore.getSnapshot(), smoothing: mode };
      const key = `${name}|${mode}`;
      put(`${key}|csv`, () => buildFrequencyCsv(result, preferences));
      put(`${key}|summary`, () => buildSummaryText(result, preferences, now));
      put(`${key}|spl`, () => splSeries(item, mode).map((series) => series.data));
      put(`${key}|impC`, () => impedanceSeries(result, 'cartesian', mode).map((series) => series.data));
      put(`${key}|impP`, () => impedanceSeries(result, 'polar', mode).map((series) => series.data));
      put(`${key}|di`, () => directivityIndexSeries(result, mode));
      put(`${key}|power`, () => powerResponseSeries(result, mode));
    }
  }
  const out: Record<string, string> = {};
  for (const [key, text] of texts) out[key] = await sha256Hex(canonicalNumbers(text));
  return out;
}

function reportNumbers(html: string): string {
  return html
    .replace(/<style>[\s\S]*?<\/style>/, '')
    .replace(/<h3>Response and power response<\/h3>[\s\S]*?(?=<h3>Warnings<\/h3>)/g, '');
}

/** Significant digits kept for every non-integer number before hashing. */
const SIGNIFICANT_DIGITS = 10;

/**
 * Rounds every decimal or exponent number in `text` to SIGNIFICANT_DIGITS
 * significant digits; integers are kept as written. Applied identically to
 * old and new outputs.
 *
 * Math.log10, Math.pow and friends may differ in the last bit between V8
 * builds: macOS arm64 and Linux x64 CI gave different full-precision digests
 * for one DI and one polar-impedance output. Ten digits is far above a
 * last-bit difference (about 1e-16 relative) and far below anything an output
 * shows: a 1e-6 dB change on a 100 dB level still moves the tenth digit.
 */
function canonicalNumbers(text: string): string {
  return text.replace(/\d+\.\d+(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+/g, (token) => (
    String(Number(Number(token).toPrecision(SIGNIFICANT_DIGITS)))
  ));
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
