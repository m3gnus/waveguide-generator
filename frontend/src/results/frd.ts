import { ARTIFACT_CONVENTIONS, ENGINEERING_NPZ_PHASOR } from '../api/conventions';
import type { Preferences } from '../prefs/preferences';
import { applySmoothing, type SmoothingValue } from './smoothing';
import { phaseSpatialSign, wrapPhaseDegrees } from './phaseConvention';
import { resultChannelFileSuffix, scopeResultChannel, type ResultPayload } from './types';

export interface PolarFrdFile {
  filename: string;
  text: string;
}

const COMMENT = '*';

// REW writes its own .frd with a comma delimiter, but its documented *accepted*
// separators are tab, space and semicolon, and VituixCAD's are the same three.
// Comma is not on either accept list and is ambiguous under a comma-decimal
// locale, so tab is the one separator both tools read for certain.
const DELIMITER = '\t';

// Per column, matching both REW's own export and the Fusion addin's proven
// VituixCAD writer (freq .6f, spl .4f, phase .4f). Frequency needs the digits: a log
// sweep puts neighbouring low-frequency points well inside 0.001 Hz of each
// other, and rounding them to three places would collide two rows into one.
const FREQUENCY_PRECISION = 6;
const LEVEL_PRECISION = 4;
const PHASE_PRECISION = 4;
const VALUE_PRECISION = LEVEL_PRECISION;

type PolarPlane = 'horizontal' | 'vertical';

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function fixed(value: number, precision = VALUE_PRECISION): string {
  const formatted = value.toFixed(precision);
  if (!/[eE]/.test(formatted)) return formatted;

  // Number#toFixed delegates to exponential notation at |value| >= 1e21.
  // FRD readers expect ordinary decimals, so expand that rare case explicitly.
  const [coefficient, exponentText] = value.toExponential(precision).split('e');
  const sign = coefficient.startsWith('-') ? '-' : '';
  const digits = coefficient.replace('-', '').replace('.', '');
  const exponent = Number(exponentText);
  return `${sign}${digits}${'0'.repeat(Math.max(0, exponent - precision))}.${'0'.repeat(precision)}`;
}

function onAxisFrequencies(result: ResultPayload): number[] {
  return result.spl_on_axis?.frequencies?.length
    ? result.spl_on_axis.frequencies
    : result.frequencies;
}

// Kept local so this standalone module does not depend on exporters.ts or mappers.ts.
function patternDb(value: unknown): number | null {
  if (Array.isArray(value)) {
    const real = Number(value[0]);
    const imaginary = Number(value[1]);
    const magnitude = Math.hypot(real, imaginary);
    return Number.isFinite(magnitude) && magnitude > 0
      ? 20 * Math.log10(magnitude)
      : null;
  }
  return finite(value) ? value : null;
}

// REW states the smoothing it applied in its own header. These files are
// smoothed too, so they have to say so -- an unlabelled smoothed curve read as
// raw is a quietly wrong measurement.
function smoothingNote(smoothing: Preferences['smoothing']): string {
  return `Smoothing: ${smoothing === 'none' ? 'none' : smoothing}`;
}

const PHASE_CONVENTION_NOTES = [
  `Phasor convention: solver and FRD pressure use ${ARTIFACT_CONVENTIONS.phasor}`,
  `Phase sign: when present, Phase(degrees) = arg(p); engineering NPZ ${ENGINEERING_NPZ_PHASOR} phase has the opposite sign`,
] as const;

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function observationNotes(result: ResultPayload): string[] {
  const directivity = record(result.metadata?.directivity);
  const observation = record(result.metadata?.observation);
  const effective = directivity.effective_distance_m ?? observation.effective_distance_m;
  const requested = directivity.requested_distance_m ?? observation.requested_distance_m;
  const distanceM = finite(effective) ? effective : finite(requested) ? requested : null;
  const distanceKind = finite(effective) ? 'effective' : finite(requested) ? 'requested' : null;
  const requestedSuffix = finite(requested) && finite(effective) && requested !== effective
    ? `; requested ${requested} m`
    : '';
  const origin = text(directivity.observation_origin) ?? text(observation.observation_origin);
  return [
    `Observation distance: ${distanceM === null ? 'unavailable' : `${distanceM} m ${distanceKind}${requestedSuffix}`}`,
    `Observation origin: ${origin ?? 'unavailable'}`,
  ];
}

function normalizationNote(result: ResultPayload, polar: boolean): string {
  if (!polar) return 'Normalization: absolute SPL re 20 µPa; level normalization none';
  const angle = record(result.metadata?.directivity).normalization_angle_degrees;
  return finite(angle)
    ? `Normalization: per-frequency polar level; 0 dB at ${angle} deg`
    : 'Normalization: per-frequency polar level; reference angle unavailable';
}

function fileText(header: string[], rows: string[]): string {
  return `${header.map((line) => `${COMMENT} ${line}`).concat(rows).join('\n')}\n`;
}

interface PropagationReference {
  distanceM: number;
  speedOfSoundMps: number;
  spatialSign: 1 | -1;
}

function propagationReference(result: ResultPayload): PropagationReference | null {
  const metadata = result.metadata?.observation;
  if (!metadata || typeof metadata !== 'object') return null;
  const distanceM = metadata.effective_distance_m ?? metadata.requested_distance_m;
  const speedOfSoundMps = metadata.sound_speed_m_per_s;
  const spatialSign = phaseSpatialSign(result.metadata?.phase_time_convention);
  return finite(distanceM) && finite(speedOfSoundMps) && distanceM >= 0 && speedOfSoundMps > 0 && spatialSign !== null
    ? { distanceM, speedOfSoundMps, spatialSign }
    : null;
}

function exportPhaseDegrees(
  phaseDegrees: number,
  frequency: number,
  reference: PropagationReference | null,
): number {
  if (!reference) return phaseDegrees;
  // Mirrors the Fusion addin's VituixCAD export. Reconcile these two
  // implementations when the addin and web exporter share a contract module.
  return wrapPhaseDegrees(
    phaseDegrees - reference.spatialSign * (360 * frequency * reference.distanceM) / reference.speedOfSoundMps,
  );
}

function propagationNote(reference: PropagationReference | null, result: ResultPayload): string {
  if (reference) {
    const operator = reference.spatialSign === 1 ? '-' : '+';
    return `Common time-of-flight removed: phase_deg ${operator} 360 * f * d / c; d=${reference.distanceM} m, c=${reference.speedOfSoundMps} m/s`;
  }
  const metadata = result.metadata?.observation;
  const distanceM = metadata?.effective_distance_m ?? metadata?.requested_distance_m;
  const speedOfSoundMps = metadata?.sound_speed_m_per_s;
  return finite(distanceM) && finite(speedOfSoundMps) && distanceM >= 0 && speedOfSoundMps > 0
    ? 'Common time-of-flight not removed: phase convention metadata is unavailable or unsupported; raw phase emitted'
    : 'Common time-of-flight not removed: directivity distance and/or speed-of-sound metadata is unavailable; raw phase emitted';
}

/**
 * Build a three-column FRD response. Only complete finite triples are emitted:
 * a blank phase field can be interpreted differently by readers, while dropping
 * the incomplete row cannot be mistaken for measured phase.
 */
export function buildOnAxisFrd(
  result: ResultPayload,
  preferences: Pick<Preferences, 'smoothing'>,
  channelId?: string,
): string {
  result = scopeResultChannel(result, channelId);
  const frequencies = onAxisFrequencies(result);
  const spl = applySmoothing(
    frequencies,
    result.spl_on_axis?.spl ?? [],
    preferences.smoothing,
  );
  const phase = result.spl_on_axis?.phase_degrees ?? [];
  // spl_on_axis is selected from the same directivity pressure field (first
  // plane, nearest zero-degree sample), so it has the same propagation reference.
  const reference = propagationReference(result);
  const rows: string[] = [];

  frequencies.forEach((frequency, index) => {
    const magnitude = spl[index];
    const phaseDegrees = phase[index];
    if (!finite(frequency) || !finite(magnitude) || !finite(phaseDegrees)) return;
    rows.push([
      fixed(frequency, FREQUENCY_PRECISION),
      fixed(magnitude, LEVEL_PRECISION),
      fixed(exportPhaseDegrees(phaseDegrees, frequency, reference), PHASE_PRECISION),
    ].join(DELIMITER));
  });

  return fileText([
    'HornLab on-axis frequency response',
    ...PHASE_CONVENTION_NOTES,
    ...observationNotes(result),
    normalizationNote(result, false),
    smoothingNote(preferences.smoothing),
    propagationNote(reference, result),
    ['Freq(Hz)', 'SPL(dB)', 'Phase(degrees)'].join(DELIMITER),
  ], rows);
}

function measuredAngles(patterns: NonNullable<ResultPayload['directivity']>[PolarPlane]): number[] {
  const angles = new Set<number>();
  patterns?.forEach((pattern) => pattern.forEach(([angle]) => {
    if (finite(angle)) angles.add(angle);
  }));
  return [...angles].sort((a, b) => a - b);
}

function valuesAtAngle(
  patterns: NonNullable<ResultPayload['directivity']>[PolarPlane],
  angle: number,
  frequencyCount: number,
): SmoothingValue[] {
  return Array.from({ length: frequencyCount }, (_, frequencyIndex) => {
    const sample = patterns?.[frequencyIndex]?.find(([sampleAngle]) => sampleAngle === angle);
    return sample ? patternDb(sample[1]) : null;
  });
}

function phaseValuesAtAngle(
  patterns: NonNullable<ResultPayload['directivity_phase']>[PolarPlane],
  angle: number,
  frequencyCount: number,
): SmoothingValue[] {
  return Array.from({ length: frequencyCount }, (_, frequencyIndex) => {
    const sample = patterns?.[frequencyIndex]?.find(([sampleAngle]) => sampleAngle === angle);
    return sample?.[1] ?? null;
  });
}

function hasAngle(
  patterns: NonNullable<ResultPayload['directivity_phase']>[PolarPlane],
  angle: number,
): boolean {
  return patterns?.some((pattern) => pattern.some(([sampleAngle]) => sampleAngle === angle)) === true;
}

// The convention the Fusion addin already uses successfully with VituixCAD --
// see hornlab-fusion-addin scripts/solve_fusion_wg_metal.py, _write_frd and
// _VITUIXCAD_PLANE_DIRS. Plane subfolders exist so the last token of the
// filename is the angle, which is what VituixCAD's measurement parser keys on.
//
// This is NOT the `Phi[mmm]Theta[ppp]` spelling: that is VituixCAD's VACS
// balloon preset for full-sphere data, a different importer.
const PLANE_DIRS: Record<PolarPlane, string> = { horizontal: 'hor', vertical: 'ver' };

/** `%g`-style: 30, -30, 7.5 -- no padding, no forced decimals. */
function angleLabel(angle: number): string {
  return String(Number(angle.toFixed(4)));
}

function polarFilename(baseName: string, plane: PolarPlane, angle: number): string {
  return `${PLANE_DIRS[plane]}/${baseName} ${angleLabel(angle)}.frd`;
}

/** Build one FRD file for every measured plane/angle, retaining legacy two-column output. */
export function buildPolarFrdSet(
  result: ResultPayload,
  preferences: Pick<Preferences, 'smoothing'>,
  jobStem: string,
  channelId?: string,
): PolarFrdFile[] {
  const suffix = resultChannelFileSuffix(result, channelId);
  result = scopeResultChannel(result, channelId);
  if (!result.directivity) return [];

  const frequencies = result.frequencies;
  const baseName = `${jobStem}${suffix}`;
  const reference = propagationReference(result);
  const files: PolarFrdFile[] = [];

  (['horizontal', 'vertical'] as const).forEach((plane) => {
    const patterns = result.directivity?.[plane];
    const phasePatterns = result.directivity_phase?.[plane];
    measuredAngles(patterns).forEach((angle) => {
      const includesPhase = hasAngle(phasePatterns, angle);
      const magnitudes = applySmoothing(
        frequencies,
        valuesAtAngle(patterns, angle, frequencies.length),
        preferences.smoothing,
      );
      const phases = includesPhase
        ? phaseValuesAtAngle(phasePatterns, angle, frequencies.length)
        : [];
      const rows: string[] = [];
      frequencies.forEach((frequency, index) => {
        const magnitude = magnitudes[index];
        if (!finite(frequency) || !finite(magnitude)) return;
        const columns = [
          fixed(frequency, FREQUENCY_PRECISION),
          fixed(magnitude, LEVEL_PRECISION),
        ];
        if (includesPhase) {
          const phaseDegrees = phases[index];
          if (!finite(phaseDegrees)) return;
          columns.push(fixed(exportPhaseDegrees(phaseDegrees, frequency, reference), PHASE_PRECISION));
        }
        rows.push(columns.join(DELIMITER));
      });
      const phaseHeader = includesPhase
        ? [propagationNote(reference, result), ['Freq(Hz)', 'SPL(dB)', 'Phase(degrees)'].join(DELIMITER)]
        : [
          'HornLab polar frequency response — magnitude-only; phase is not available and is intentionally omitted',
          ['Freq(Hz)', 'SPL(dB)'].join(DELIMITER),
        ];
      files.push({
        filename: polarFilename(baseName, plane, angle),
        text: fileText([
          ...(includesPhase ? ['HornLab polar frequency response'] : []),
          `Plane: ${plane}, angle ${angle} deg`,
          ...PHASE_CONVENTION_NOTES,
          ...observationNotes(result),
          normalizationNote(result, true),
          smoothingNote(preferences.smoothing),
          ...phaseHeader,
        ], rows),
      });
    });
  });

  return files;
}
