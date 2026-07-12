import { applySmoothing } from '../../results/smoothing.js';
import {
  isRhoCNormalizedImpedance,
  resolvePhaseReferenceDistance,
  resolvePhaseTimeConvention,
} from '../../results/conventions.js';
import { extractPerPlaneDI } from './diHelpers.js';

export const CHART_TYPES = Object.freeze([
  Object.freeze({ key: 'directivity_map', label: 'Polar Directivity Map' }),
  Object.freeze({ key: 'impedance', label: 'Acoustic Impedance' }),
  Object.freeze({ key: 'directivity_index', label: 'Directivity Index' }),
  Object.freeze({ key: 'frequency_response', label: 'Frequency Response (SPL On-Axis)' }),
]);

// Panel-selectable chart list: the modal shows CHART_TYPES as-is (all planes
// in one directivity render); dock panels can additionally show a single
// directivity plane. `planes` filters the directivity payload client-side —
// the render endpoints are unchanged.
export const PANEL_CHART_TYPES = Object.freeze([
  Object.freeze({ key: 'directivity_map_h', label: 'Directivity Map (H)', planes: ['horizontal'] }),
  Object.freeze({ key: 'directivity_map_v', label: 'Directivity Map (V)', planes: ['vertical'] }),
  Object.freeze({ key: 'directivity_map', label: 'Directivity Map (All planes)' }),
  Object.freeze({ key: 'frequency_response', label: 'Frequency Response (SPL On-Axis)' }),
  Object.freeze({ key: 'directivity_index', label: 'Directivity Index' }),
  Object.freeze({ key: 'impedance', label: 'Acoustic Impedance' }),
  Object.freeze({ key: 'summary', label: 'Simulation Summary' }),
]);

export function isDirectivityChartKey(chartKey) {
  return chartKey === 'directivity_map' || String(chartKey || '').startsWith('directivity_map_');
}

export function directivityPlanesForChartKey(chartKey) {
  const chart = PANEL_CHART_TYPES.find((item) => item.key === chartKey);
  return chart?.planes || null;
}

function filterDirectivityPlanes(directivity, planes) {
  if (!planes) return directivity;
  return Object.fromEntries(
    Object.entries(directivity).filter(([plane]) => planes.includes(plane))
  );
}

const DIRECTIVITY_PLANE_ORDER = ['horizontal', 'vertical', 'diagonal'];

function normalizeDirectivityPayload(directivity) {
  if (!directivity || typeof directivity !== 'object' || Array.isArray(directivity)) {
    return {};
  }

  const entries = Object.entries(directivity).filter(([, patterns]) => Array.isArray(patterns));
  entries.sort(([a], [b]) => {
    const aKey = String(a || '')
      .trim()
      .toLowerCase();
    const bKey = String(b || '')
      .trim()
      .toLowerCase();
    const aIndex = DIRECTIVITY_PLANE_ORDER.indexOf(aKey);
    const bIndex = DIRECTIVITY_PLANE_ORDER.indexOf(bKey);
    if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
    if (aIndex !== -1) return -1;
    if (bIndex !== -1) return 1;
    return aKey.localeCompare(bKey);
  });
  return Object.fromEntries(entries);
}

export function hasDirectivityPatterns(directivity) {
  return Object.values(directivity || {}).some(
    (patterns) => Array.isArray(patterns) && patterns.length > 0
  );
}

function smoothSeries(frequencies, values, smoothing) {
  if (smoothing === 'none') return values;
  return applySmoothing(frequencies, values, smoothing);
}

function smoothDiSeries(frequencies, di, smoothing) {
  if (smoothing === 'none') return di;
  if (Array.isArray(di)) return applySmoothing(frequencies, di, smoothing);
  if (!di || typeof di !== 'object') return di;

  const smoothed = {};
  for (const [plane, values] of Object.entries(di)) {
    smoothed[plane] = applySmoothing(frequencies, values, smoothing);
  }
  return smoothed;
}

function extractLineSeries(results, smoothing = 'none') {
  const splData = results?.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  const diData = results?.di || {};
  const diFrequencies = diData.frequencies || frequencies;
  const impedanceData = results?.impedance || {};
  const impedanceFrequencies = impedanceData.frequencies || frequencies;

  return {
    frequencies,
    spl: smoothSeries(frequencies, splData.spl || [], smoothing),
    phaseDegrees: splData.phase_degrees || [],
    di: smoothDiSeries(diFrequencies, extractPerPlaneDI(diData), smoothing),
    diFrequencies,
    impedanceFrequencies,
    impedanceReal: smoothSeries(impedanceFrequencies, impedanceData.real || [], smoothing),
    impedanceImaginary: smoothSeries(
      impedanceFrequencies,
      impedanceData.imaginary || [],
      smoothing
    ),
  };
}

function resolveReference(reference) {
  if (!reference || typeof reference !== 'object') return null;
  if (reference.results && typeof reference.results === 'object') {
    return { results: reference.results, label: reference.label };
  }
  return { results: reference, label: reference.label };
}

function buildLineReference(reference, smoothing) {
  const resolved = resolveReference(reference);
  if (!resolved) return null;

  const series = extractLineSeries(resolved.results, smoothing);
  const payload = {
    label: resolved.label ?? null,
    frequencies: series.frequencies,
    spl: series.spl,
    di: series.di,
    di_frequencies: series.diFrequencies,
    impedance_frequencies: series.impedanceFrequencies,
    impedance_real: series.impedanceReal,
    impedance_imaginary: series.impedanceImaginary,
  };

  if (isRhoCNormalizedImpedance(resolved.results)) {
    payload.impedance_units = 'Z/(rho*c)';
    payload.impedance_normalization = 'rho_c';
  }
  return payload;
}

export function buildLineChartsPayload(
  results,
  { smoothing = 'none', theme, reference = null } = {}
) {
  const series = extractLineSeries(results, smoothing);
  const payload = {
    frequencies: series.frequencies,
    spl: series.spl,
    phase_degrees: series.phaseDegrees,
    phase_reference_distance_m: resolvePhaseReferenceDistance(results),
    phase_time_convention: resolvePhaseTimeConvention(results),
    di: series.di,
    di_frequencies: series.diFrequencies,
    impedance_frequencies: series.impedanceFrequencies,
    impedance_real: series.impedanceReal,
    impedance_imaginary: series.impedanceImaginary,
    theme,
  };

  if (isRhoCNormalizedImpedance(results)) {
    payload.impedance_units = 'Z/(rho*c)';
    payload.impedance_normalization = 'rho_c';
  }

  const referencePayload = buildLineReference(reference, smoothing);
  if (referencePayload) payload.reference = referencePayload;
  return payload;
}

export function buildDirectivityPayload(
  results,
  { referenceLevel, theme, reference = null, planes = null } = {}
) {
  const splData = results?.spl_on_axis || {};
  const payload = {
    frequencies: splData.frequencies || [],
    directivity: filterDirectivityPlanes(normalizeDirectivityPayload(results?.directivity), planes),
    reference_level: referenceLevel,
    theme,
  };

  const resolvedReference = resolveReference(reference);
  if (resolvedReference) {
    const referenceSpl = resolvedReference.results?.spl_on_axis || {};
    payload.reference_frequencies = referenceSpl.frequencies || [];
    payload.reference_directivity = filterDirectivityPlanes(
      normalizeDirectivityPayload(resolvedReference.results?.directivity),
      planes
    );
    payload.reference_label = resolvedReference.label ?? null;
  }

  return payload;
}

async function requestChartImage(url, payload, resultKey) {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      return {
        ok: false,
        [resultKey]: resultKey === 'charts' ? {} : null,
        status: response.status,
        detail,
        kind: response.status === 503 ? 'matplotlib-missing' : 'http',
      };
    }

    const data = await response.json();
    return {
      ok: true,
      [resultKey]: data[resultKey] || (resultKey === 'charts' ? {} : null),
      status: response.status ?? 200,
      detail: '',
      kind: null,
    };
  } catch (error) {
    return {
      ok: false,
      [resultKey]: resultKey === 'charts' ? {} : null,
      status: 0,
      detail: error?.message || String(error),
      kind: 'network',
    };
  }
}

export function requestLineCharts(backendUrl, payload) {
  return requestChartImage(`${backendUrl}/api/render-charts`, payload, 'charts');
}

export function requestDirectivityMap(backendUrl, payload) {
  return requestChartImage(`${backendUrl}/api/render-directivity`, payload, 'image');
}
