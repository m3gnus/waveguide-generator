import type { JobItem } from '../api/jobsSocket';
import { runDisplayName } from '../prefs/preferences';
import type { ResultPayload } from './types';

/** One label/value line. `title` becomes the row's hover text when set. */
export interface SummaryRow {
  label: string;
  value: string;
  title?: string;
}

/** A titled block of rows. `tone` marks a block the reader must not skip. */
export interface SummaryGroup {
  title: string;
  tone?: 'warning';
  rows: SummaryRow[];
}

export interface SummaryContext {
  /** The channel-scoped payload the card displays. */
  result: ResultPayload;
  /** The unscoped payload, when the result is a CAD import wrapper. */
  wrapper?: ResultPayload;
  /** The run record from the jobs socket, when the panel knows it. */
  job?: JobItem | null;
  /** The active drive channel, when the wrapper carries channels. */
  channelId?: string | null;
}

/** Grouped provenance for the Simulation Summary card. */
export function summaryGroups(_context: SummaryContext): SummaryGroup[] {
  const context = _context;
  const { result, wrapper = result, job } = context;
  const resultMetadata = object(result.metadata) ?? {};
  const wrapperMetadata = object(wrapper.metadata) ?? {};
  const metadata = (key: string): unknown => resultMetadata[key] ?? wrapperMetadata[key];
  const groups: SummaryGroup[] = [];

  const run: SummaryRow[] = [];
  if (job) row(run, 'Name', runDisplayName(job));
  const completed = string(job?.completed_at) ?? string(metadata('timestamp'));
  if (completed) row(run, 'Completed', localDateTime(completed));
  const jobSolveTime = finite(job?.solve_wall_time_seconds);
  const batchSolveTime = finite(object(metadata('performance'))?.total_time_seconds);
  if (jobSolveTime !== undefined) {
    row(run, 'Solve time', duration(jobSolveTime));
  } else if (batchSolveTime !== undefined) {
    const channelCount = channelIds(wrapper).length;
    row(
      run,
      'Solve time',
      duration(batchSolveTime),
      channelCount > 1
        ? `Shared batch time for all ${channelCount} imported drive channels; not an isolated channel solve.`
        : undefined,
    );
  }
  const queueWait = elapsedSeconds(job?.queued_at, job?.started_at);
  if (queueWait !== undefined && queueWait >= 1) row(run, 'Queue wait', duration(queueWait));
  if (job && job.status !== 'complete') row(run, 'Status', job.status);
  group(groups, 'Run', run);

  const sweep: SummaryRow[] = [];
  const frequencies = Array.isArray(result.frequencies) ? result.frequencies.filter(isFiniteNumber) : [];
  if (frequencies.length) row(sweep, 'Range', `${frequency(frequencies[0])} – ${frequency(frequencies.at(-1)!)}`);
  if (Array.isArray(result.frequencies)) row(sweep, 'Points', result.frequencies.length.toLocaleString());
  const frequencySource = string(metadata('frequency_source'));
  const frequencySpacing = string(metadata('frequency_spacing'));
  if (frequencySource === 'explicit_list') row(sweep, 'Spacing', 'explicit list');
  else if (frequencySource === 'generated_grid' && frequencySpacing === 'log') row(sweep, 'Spacing', 'logarithmic');
  else if (frequencySource === 'generated_grid' && frequencySpacing === 'linear') row(sweep, 'Spacing', 'linear');
  group(groups, 'Sweep', sweep);

  const solve: SummaryRow[] = [];
  const engine = firstString(metadata('engine'), metadata('solver_backend'), metadata('solver_mode'));
  if (engine) row(solve, 'Engine', readableToken(engine));
  const solvePath = string(metadata('solve_path'));
  if (solvePath) row(solve, 'Path', readableSolvePath(solvePath), string(metadata('solve_path_reason')));
  const symmetry = object(metadata('symmetry'));
  const quadrants = finite(symmetry?.resolved_quadrants);
  if (quadrants !== undefined) {
    const autoResolved = string(symmetry?.requested) === 'auto' && object(symmetry?.auto_resolution) !== undefined;
    row(solve, 'Symmetry', `${quadrantLabel(quadrants)}${autoResolved ? ' (server resolved)' : ''}`);
  }
  const device = string(object(metadata('device_interface'))?.selected);
  if (device) row(solve, 'Device', device);
  if (metadata('infinite_baffle') !== undefined && metadata('infinite_baffle') !== null) {
    row(solve, 'Infinite baffle', 'enabled');
  }
  const metadataMesh = object(metadata('mesh_stats'));
  if (metadata('mesh_cache_hit') === true || metadataMesh?.mesh_cache_hit === true) row(solve, 'Mesh cache', 'reused');
  group(groups, 'Solve', solve);

  const meshStats = metadataMesh ?? object(job?.mesh_stats);
  const mesh: SummaryRow[] = [];
  const triangleCount = finite(meshStats?.triangle_count);
  if (triangleCount !== undefined) row(mesh, 'Triangles', integer(triangleCount));
  const multiplier = finite(meshStats?.domain_multiplier);
  const fullDomainCount = finite(meshStats?.full_domain_triangle_count);
  if (multiplier !== undefined && multiplier > 1 && fullDomainCount !== undefined) {
    row(mesh, 'Full domain', integer(fullDomainCount), 'Symmetry-expanded equivalent of the solved mesh.');
  }
  const vertexCount = finite(meshStats?.vertex_count);
  if (vertexCount !== undefined) row(mesh, 'Vertices', integer(vertexCount));
  const maxEdgeMm = finite(meshStats?.max_edge_mm);
  if (maxEdgeMm !== undefined) row(mesh, 'Max edge', length(maxEdgeMm / 1_000));
  const dimensions = object(meshStats?.dimensions_m);
  const width = finite(dimensions?.width);
  const height = finite(dimensions?.height);
  const depth = finite(dimensions?.depth);
  if (width !== undefined && height !== undefined && depth !== undefined) {
    row(mesh, 'Size', `${length(width)} × ${length(height)} × ${length(depth)}`);
  }
  group(groups, 'Mesh', mesh);

  const measurement: SummaryRow[] = [];
  const directivityMetadata = object(metadata('directivity'));
  const observation = object(metadata('observation'));
  const effectiveDistance = firstFinite(directivityMetadata?.effective_distance_m, observation?.effective_distance_m);
  const requestedDistance = firstFinite(directivityMetadata?.requested_distance_m, observation?.requested_distance_m);
  if (effectiveDistance !== undefined) {
    const origin = firstString(directivityMetadata?.observation_origin, observation?.observation_origin);
    const requested = requestedDistance !== undefined && Math.abs(requestedDistance - effectiveDistance) > 1e-9
      ? ` (requested ${length(requestedDistance)})`
      : '';
    row(measurement, 'Distance', `${length(effectiveDistance)}${origin ? ` from ${origin}` : ''}${requested}`);
  }
  const angleRange = finitePair(directivityMetadata?.angle_range_degrees);
  if (angleRange) row(measurement, 'Polar sweep', `${angle(angleRange[0])} – ${angle(angleRange[1])}`);
  const resolvedStep = finite(directivityMetadata?.angular_step_degrees);
  const requestedStep = finite(directivityMetadata?.requested_angular_step_degrees);
  const sampleCount = finite(directivityMetadata?.sample_count);
  if (resolvedStep !== undefined && sampleCount !== undefined) {
    const differs = requestedStep !== undefined
      && Math.abs(requestedStep - resolvedStep) > Math.max(1e-9, Math.abs(requestedStep) * 1e-7);
    const step = differs
      ? `${angle(resolvedStep)} resolved (requested ${angle(requestedStep)})`
      : `${angle(resolvedStep)} step`;
    row(measurement, 'Sampling', `${step}, ${integer(sampleCount)} samples`);
  }
  const planes = sampledPlanes(result.directivity);
  if (planes.length) row(measurement, 'Planes', planes.join(' · '));
  const normalization = finite(directivityMetadata?.normalization_angle_degrees);
  if (normalization !== undefined) row(measurement, 'Normalization', angle(normalization));
  const diagonal = finite(directivityMetadata?.diagonal_angle_degrees);
  if (diagonal !== undefined && planes.includes('diagonal')) row(measurement, 'Diagonal plane', angle(diagonal));
  const balloon = object(result.balloon);
  const balloonFrequencies = array(balloon?.frequencies);
  const theta = array(balloon?.theta_deg);
  const phi = array(balloon?.phi_deg);
  if (balloon && balloonFrequencies && theta && phi) {
    const extent = balloon.hemisphere === true ? 'hemisphere' : balloon.hemisphere === false ? 'full sphere' : null;
    const grid = `${theta.length.toLocaleString()} × ${phi.length.toLocaleString()} × ${balloonFrequencies.length.toLocaleString()} freq`;
    row(measurement, 'Balloon', `${grid}${extent ? ` · ${extent}` : ''}`);
  } else {
    const balloonStatus = balloonStatusText(object(metadata('balloon_sampling')));
    if (balloonStatus) row(measurement, 'Balloon', balloonStatus);
  }
  group(groups, 'Measurement', measurement);

  const conventions: SummaryRow[] = [];
  const impedanceUnits = string(metadata('impedance_units'));
  if (result.impedance && impedanceUnits) row(conventions, 'Impedance', impedanceUnits);
  const phaseConvention = string(metadata('phase_time_convention'));
  if (phaseConvention) row(conventions, 'Phase', phaseConvention);
  if (result.beam_shape) {
    const diDomain = string(result.beam_shape.di_domain);
    if (diDomain) row(conventions, 'DI domain', diDomain);
  }
  group(groups, 'Conventions', conventions);

  const importRows: SummaryRow[] = [];
  if (string(wrapperMetadata.geometry_type) === 'imported') {
    row(importRows, 'Geometry', 'imported');
    const channels = channelIds(wrapper);
    if (channels.length > 1 && context.channelId) {
      row(importRows, 'Channel', `${context.channelId} · ${channels.length.toLocaleString()} channels`);
    }
    const ingestId = string(wrapperMetadata.ingest_id);
    if (ingestId) row(importRows, 'Ingest', ingestId);
    const manifest = string(wrapperMetadata.manifest_sha256);
    if (manifest) row(importRows, 'Manifest', manifest.slice(0, 12), manifest);
  }
  group(groups, 'Import', importRows);

  const runWarnings = strings(metadata('warnings'));
  const failures = failureTexts(metadata('failures'));
  const failureCount = finite(metadata('failure_count')) ?? failures.length;
  const partial = metadata('partial_success') === true;
  const meshWarnings = strings(meshStats?.warnings);
  const integrity = object(meshStats?.integrity);
  const meshValidation = object(metadata('mesh_validation'));
  const validationWarnings = array(meshValidation?.warnings);
  // The mesh builder's warnings are the ones that actually fire in practice
  // ("Large solve mesh: …"), and the solver does not copy them into
  // metadata.warnings. Showing only the run warnings therefore left the
  // Diagnostics group announcing a warned mesh while withholding the sentence
  // that says what is wrong — the exact gap this card exists to close.
  const warningTexts = [...runWarnings, ...meshWarnings.filter((text) => !runWarnings.includes(text))];
  const warningCount = finite(metadata('warning_count')) ?? warningTexts.length;
  const explicitValidationState = firstString(meshValidation?.state, meshValidation?.status);
  const validationFailed = integrity?.valid === false || meshValidation?.valid === false
    || explicitValidationState === 'failed' || explicitValidationState === 'invalid';
  const validationWarned = meshWarnings.length > 0
    || Boolean(validationWarnings?.length)
    || (finite(meshValidation?.warning_count) ?? 0) > 0
    || explicitValidationState === 'warning' || explicitValidationState === 'warnings';
  if (validationFailed || validationWarned || warningCount > 0 || failureCount > 0 || partial) {
    const diagnostics: SummaryRow[] = [];
    const validationMode = string(meshValidation?.mode);
    const validationState = validationFailed ? 'failed' : validationWarned ? 'warnings' : integrity?.valid === true ? 'passed' : null;
    if (validationState) row(diagnostics, 'Mesh validation', `${validationState}${validationMode ? ` · ${validationMode} mode` : ''}`);
    if (partial) row(diagnostics, 'Solve status', 'partial');
    cappedRows(diagnostics, 'Warning', warningTexts);
    cappedRows(diagnostics, 'Failed', failures);
    if (warningCount > 0 && !warningTexts.length) row(diagnostics, 'Warning', `${integer(warningCount)} reported`);
    if (failureCount > 0 && !failures.length) row(diagnostics, 'Failed', `${integer(failureCount)} reported`);
    group(groups, 'Diagnostics', diagnostics, 'warning');
  }

  return groups;
}

/** The same content as plain text, for the clipboard. */
export function summaryText(groups: SummaryGroup[]): string {
  if (!groups.length) return '';
  return `${groups.map(({ title, rows }) => [title.toUpperCase(), ...rows.map(({ label, value }) => `  ${label}: ${value}`)].join('\n')).join('\n\n')}\n`;
}

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function array(value: unknown): unknown[] | undefined {
  return Array.isArray(value) ? value : undefined;
}

function string(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const text = value.trim();
  return text && !['—', 'undefined', 'unknown'].includes(text.toLowerCase()) ? text : undefined;
}

function firstString(...values: unknown[]): string | undefined {
  return values.map(string).find((value) => value !== undefined);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function finite(value: unknown): number | undefined {
  return isFiniteNumber(value) ? value : undefined;
}

function firstFinite(...values: unknown[]): number | undefined {
  return values.map(finite).find((value) => value !== undefined);
}

function finitePair(value: unknown): [number, number] | undefined {
  return Array.isArray(value) && value.length >= 2 && isFiniteNumber(value[0]) && isFiniteNumber(value[1])
    ? [value[0], value[1]]
    : undefined;
}

function row(rows: SummaryRow[], label: string, value: string | undefined, title?: string): void {
  if (!value?.trim()) return;
  rows.push(title ? { label, value, title } : { label, value });
}

function group(groups: SummaryGroup[], title: string, rows: SummaryRow[], tone?: 'warning'): void {
  if (!rows.length) return;
  groups.push(tone ? { title, tone, rows } : { title, rows });
}

function integer(value: number): string {
  return Math.round(value).toLocaleString();
}

function frequency(value: number): string {
  if (!Number.isFinite(value)) return '';
  return Math.abs(value) >= 1_000
    ? `${(value / 1_000).toFixed(Math.abs(value) >= 10_000 ? 1 : 2)} kHz`
    : `${Math.round(value)} Hz`;
}

function angle(value: number): string {
  const rounded = Math.round(value);
  return `${Math.abs(value - rounded) <= 1e-9 ? rounded : value.toFixed(1)}°`;
}

function length(valueM: number): string {
  const magnitude = Math.abs(valueM);
  if (magnitude >= 1) return `${valueM.toFixed(magnitude >= 10 ? 1 : 2)} m`;
  const valueMm = valueM * 1_000;
  const magnitudeMm = Math.abs(valueMm);
  return `${valueMm.toFixed(magnitudeMm >= 100 ? 0 : magnitudeMm >= 10 ? 1 : 2)} mm`;
}

function duration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}m ${String(rounded % 60).padStart(2, '0')}s`;
}

function elapsedSeconds(start: unknown, end: unknown): number | undefined {
  const startText = string(start);
  const endText = string(end);
  if (!startText || !endText) return undefined;
  const elapsed = (Date.parse(endText) - Date.parse(startText)) / 1_000;
  return Number.isFinite(elapsed) && elapsed >= 0 ? elapsed : undefined;
}

function localDateTime(value: string): string | undefined {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return undefined;
  const pad = (part: number): string => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function readableToken(value: string): string {
  return value.replaceAll('_', ' ');
}

function readableSolvePath(value: string): string {
  if (value === 'full-3d' || value === 'full_3d') return 'full 3D';
  if (value === 'axisymmetric-meridian' || value === 'circsym') return 'axisymmetric meridian';
  return readableToken(value).replaceAll('-', ' ');
}

function quadrantLabel(value: number): string {
  if (value === 1) return 'quadrant 1 · quarter domain';
  if (value === 12 || value === 14) return `quadrants ${value} · half domain`;
  if (value === 1234) return 'quadrants 1234 · full domain';
  return `quadrants ${integer(value)}`;
}

function sampledPlanes(value: unknown): string[] {
  const directivity = object(value);
  if (!directivity) return [];
  const available = Object.entries(directivity)
    .filter(([, samples]) => Array.isArray(samples) && samples.length > 0)
    .map(([plane]) => plane);
  return ['horizontal', 'vertical', ...available.filter((plane) => plane !== 'horizontal' && plane !== 'vertical')]
    .filter((plane, index, planes) => available.includes(plane) && planes.indexOf(plane) === index);
}

function balloonStatusText(sampling: Record<string, unknown> | undefined): string | undefined {
  const status = string(sampling?.status);
  if (status === 'disabled') return 'not requested';
  if (status === 'backend_unsupported') return 'backend unsupported';
  if (status === 'missing_result') return 'requested, none returned';
  if (status && status !== 'available') return readableToken(status);
  if (sampling?.requested === false) return 'not requested';
  if (sampling?.requested === true && sampling.available === false) {
    return sampling.configured === false ? 'backend unsupported' : 'requested, none returned';
  }
  return undefined;
}

function channelIds(result: ResultPayload): string[] {
  const channels = object(result.channels);
  return channels ? Object.keys(channels) : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(string).filter((item): item is string => item !== undefined) : [];
}

function failureTexts(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const failure = object(entry);
    if (!failure) return [];
    const frequencyHz = finite(failure.frequency_hz);
    const stage = string(failure.stage);
    const code = string(failure.code);
    const detail = string(failure.detail);
    if (frequencyHz === undefined || !stage || !code || !detail) return [];
    return [`${frequency(frequencyHz)} · ${stage} · ${code}: ${detail}`];
  });
}

function cappedRows(rows: SummaryRow[], label: string, values: string[]): void {
  values.slice(0, 4).forEach((value) => row(rows, label, value));
  if (values.length > 4) row(rows, label, `+${values.length - 4} more`);
}
