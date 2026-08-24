import { compareSelection, provisionalResults, type ResultData } from './results';

/**
 * Reference-compares own properties. Nested values are compared by identity,
 * which is deliberately conservative: a freshly parsed payload always looks
 * changed, so this can only ever suppress a notification that carried nothing.
 */
function shallowEqual(a: object, b: object): boolean {
  if (a === b) return true;
  const keys = Object.keys(a) as (keyof typeof a)[];
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((key) => Object.is(a[key], (b as typeof a)[key]));
}

export type JobStatus = 'queued' | 'running' | 'complete' | 'error' | 'cancelled';
export type JobsConnection = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected';
export interface AutoExportFormatStatus {
  status: 'complete' | 'failed';
  attempted_at: string;
  reason?: string;
}

export interface JobItem {
  id: string;
  run_number: number;
  parent_job_id: string | null;
  status: JobStatus;
  progress: number;
  stage: string | null;
  stage_message: string | null;
  created_at: string;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  config_summary: Record<string, unknown>;
  solve_options: {
    engine: string;
    symmetry: string;
    frequency_range: number[] | null;
    num_frequencies: number | null;
    frequency_spacing: 'log' | 'linear';
    frequencies_hz: number[] | null;
    verbose: boolean;
    mesh_validation_mode: 'warn' | 'strict' | 'off';
    polar_config: Record<string, unknown>;
    stage_delay_ms: number;
  };
  has_results: boolean;
  has_mesh_artifact: boolean;
  /** Whether the run produced a port-exit radiation-impedance matrix, i.e. it
   * ran the passive-cardioid campaign and the archive is still on disk. */
  has_radiation_impedance_artifact?: boolean;
  radiation_impedance_artifact_bytes?: number | null;
  persistence_warnings?: string[];
  has_pressure_basis_artifact?: boolean;
  pressure_basis_artifact_bytes?: number | null;
  field_plane_available?: boolean;
  field_trace_bytes?: number | null;
  unavailable_reason?: string | null;
  label: string | null;
  error_message: string | null;
  cancellation_requested: boolean;
  mesh_stats: Record<string, unknown> | null;
  script_snapshot: Record<string, unknown> | null;
  design_revision: number;
  polar_grid: Record<string, unknown>;
  rating: number | null;
  exported_files: string[];
  auto_export_completed_at: string | null;
  auto_export_formats: Record<string, AutoExportFormatStatus>;
  /** When this run was written to the run archive, if it has been. */
  archived_at?: string | null;
  raw_results_file: string | null;
  mesh_artifact_file: string | null;
  results_discarded_at?: string | null;
  mesh_discarded_at?: string | null;
  log_tail: string[];
  design_availability?: {
    reopenable: boolean;
    source: 'v2-snapshot' | 'v1-design-state' | 'v1-mesher-payload' | 'cad-import' | 'none';
    reason_code: 'ok' | 'recovered' | 'imported_geometry' | 'freeform_legacy_design' | 'no_stored_design' | 'unreadable_design';
    reason: string | null;
    note: string | null;
  } | null;
  symmetry?: Record<string, unknown>;
  solve_path?: 'full-3d' | 'axisymmetric-meridian' | null;
  axisymmetric_eligibility_reasons?: string[];
  solve_wall_time_seconds?: number | null;
  /** Where an imported run came from. Absent on parametric runs. */
  cad_source?: CadSource | null;
}

/** The CAD provenance a run keeps so its archive stays traceable. */
export interface CadIdentityProvenance {
  schema_version: 1;
  ingest_id: string;
  selected_instance_id: string | null;
  solver_anchor_instance_id: string | null;
  instances: Array<{
    instance_id: string;
    design_id: string | null;
    body_object_ids: string[];
    assembly_from_link: number[][];
    source_ids: string[];
    default_drive_channel_ids: string[];
  }>;
  drive_channels: Array<{
    drive_channel_id: string;
    source_ids: string[];
    instance_ids: string[];
  }>;
}

export interface CadSource {
  ingest_id: string | null;
  design_id: string | null;
  lineage_id: string | null;
  /** The folder this design's runs are archived under. */
  archive_stem: string | null;
  manifest_sha256: string | null;
  document_name: string | null;
  return_state_hash: string | null;
  identity?: CadIdentityProvenance | null;
}

export interface JobsSnapshot {
  connection: JobsConnection;
  epoch: number | null;
  cursor: number | null;
  jobs: JobItem[];
  error: string | null;
}

interface SocketEvent { data?: unknown }

export interface JobsWebSocketLike {
  readyState: number;
  onopen: ((event: SocketEvent) => void) | null;
  onmessage: ((event: SocketEvent) => void) | null;
  onerror: ((event: SocketEvent) => void) | null;
  onclose: ((event: SocketEvent) => void) | null;
  send(data: string): void;
  close(): void;
}

export type JobsWebSocketFactory = (url: string) => JobsWebSocketLike;
export type JobsFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

interface HelloMessage {
  v: 1;
  kind: 'hello';
  epoch: number;
  heartbeatSec: number;
}

interface SnapshotMessage {
  v: 1;
  kind: 'snapshot';
  epoch?: number;
  cursor: number;
  jobs: JobItem[];
}

interface EventMessage {
  v: 1;
  kind: 'event';
  epoch?: number;
  cursor: number;
  jobId: string;
  type: 'queued' | 'started' | 'progress' | 'stage' | 'log' | 'completed' | 'failed' | 'cancelled' | 'deleted' | 'metadata';
  payload?: Record<string, unknown>;
}

interface PartialResultMessage {
  v: 1;
  kind: 'partialResult';
  epoch?: number;
  jobId: string;
  revision: number;
  snapshot?: boolean;
  result: ResultData;
}

type JsonRecord = Record<string, unknown>;
type EventType = EventMessage['type'];

const EVENT_TYPES = new Set<EventType>([
  'queued', 'started', 'progress', 'stage', 'log', 'completed', 'failed',
  'cancelled', 'deleted', 'metadata',
]);
const FORBIDDEN_JSON_KEYS = new Set(['__proto__', 'prototype', 'constructor']);

function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasOwn(record: JsonRecord, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isNullableNumber(value: unknown): boolean {
  return value === null || isFiniteNumber(value);
}

function isNumberArray(value: unknown, nullable = false): value is number[] {
  return Array.isArray(value) && value.every(nullable ? isNullableNumber : isFiniteNumber);
}

/** Reject non-JSON values and keys that are hazardous when records are merged. */
function isSafeJson(value: unknown, depth = 0): boolean {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (isFiniteNumber(value)) return true;
  if (depth >= 40) return false;
  if (Array.isArray(value)) return value.every((item) => isSafeJson(item, depth + 1));
  if (!isRecord(value)) return false;
  return Object.entries(value).every(([key, item]) => (
    !FORBIDDEN_JSON_KEYS.has(key) && isSafeJson(item, depth + 1)
  ));
}

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value));
}

function isNullableTimestamp(value: unknown): value is string | null {
  return value === null || isTimestamp(value);
}

function isCadSource(value: unknown): value is CadSource {
  return isRecord(value)
    && (['ingest_id', 'design_id', 'lineage_id', 'archive_stem', 'manifest_sha256', 'document_name', 'return_state_hash'] as const)
      .every((key) => !hasOwn(value, key) || isNullableString(value[key]))
    && (!hasOwn(value, 'identity') || value.identity === null || (
      isRecord(value.identity)
      && value.identity.schema_version === 1
      && typeof value.identity.ingest_id === 'string'
      && (value.identity.selected_instance_id === null || typeof value.identity.selected_instance_id === 'string')
      && (value.identity.solver_anchor_instance_id === null || typeof value.identity.solver_anchor_instance_id === 'string')
      && Array.isArray(value.identity.instances)
      && Array.isArray(value.identity.drive_channels)
    ));
}

function isAutoExportFormats(value: unknown): value is JobItem['auto_export_formats'] {
  // Older rows and future exporters may attach fields beyond the current
  // complete/failed UI projection. The UI treats unrecognised entries as not
  // complete, so retaining safe objects is the forward-compatible choice.
  return isRecord(value)
    && isSafeJson(value)
    && Object.values(value).every((entry) => isRecord(entry));
}

/**
 * Snapshot rows are durable application state. Validate all fields consumed by
 * the UI and reject unsafe nested JSON before a row reaches sorting or merges.
 * Unknown safe fields are retained so additive server changes stay compatible.
 */
function isJobItem(value: unknown): value is JobItem {
  if (!isRecord(value) || !isSafeJson(value)) return false;
  if (typeof value.id !== 'string' || value.id.length === 0) return false;
  if (!Number.isSafeInteger(value.run_number) || Number(value.run_number) < 1) return false;
  if (!(value.parent_job_id === null || typeof value.parent_job_id === 'string')) return false;
  if (!['queued', 'running', 'complete', 'error', 'cancelled'].includes(String(value.status))) return false;
  if (!isFiniteNumber(value.progress) || value.progress < 0 || value.progress > 1) return false;
  if (!isNullableString(value.stage) || !isNullableString(value.stage_message)) return false;
  if (!isTimestamp(value.created_at) || !isTimestamp(value.queued_at)) return false;
  if (!isNullableTimestamp(value.started_at) || !isNullableTimestamp(value.completed_at)) return false;
  if (!isRecord(value.config_summary) || !isRecord(value.solve_options)) return false;
  if (typeof value.has_results !== 'boolean' || typeof value.has_mesh_artifact !== 'boolean') return false;
  if (hasOwn(value, 'has_radiation_impedance_artifact')
    && typeof value.has_radiation_impedance_artifact !== 'boolean') return false;
  if (hasOwn(value, 'radiation_impedance_artifact_bytes') && !(
    value.radiation_impedance_artifact_bytes === null || isNonNegativeInteger(value.radiation_impedance_artifact_bytes)
  )) return false;
  if (hasOwn(value, 'persistence_warnings') && !isStringArray(value.persistence_warnings)) return false;
  if (hasOwn(value, 'has_pressure_basis_artifact') && typeof value.has_pressure_basis_artifact !== 'boolean') return false;
  if (hasOwn(value, 'pressure_basis_artifact_bytes') && !(
    value.pressure_basis_artifact_bytes === null || isNonNegativeInteger(value.pressure_basis_artifact_bytes)
  )) return false;
  if (hasOwn(value, 'field_plane_available') && typeof value.field_plane_available !== 'boolean') return false;
  if (hasOwn(value, 'field_trace_bytes') && !(
    value.field_trace_bytes === null || isNonNegativeInteger(value.field_trace_bytes)
  )) return false;
  if (hasOwn(value, 'unavailable_reason') && !isNullableString(value.unavailable_reason)) return false;
  if (!isNullableString(value.label) || !isNullableString(value.error_message)) return false;
  if (typeof value.cancellation_requested !== 'boolean') return false;
  if (!(value.mesh_stats === null || isRecord(value.mesh_stats))) return false;
  if (!(value.script_snapshot === null || isRecord(value.script_snapshot))) return false;
  if (!isNonNegativeInteger(value.design_revision) || !isRecord(value.polar_grid)) return false;
  if (!(value.rating === null || (
    Number.isSafeInteger(value.rating) && Number(value.rating) >= 0 && Number(value.rating) <= 5
  ))) return false;
  if (!isStringArray(value.exported_files)) return false;
  if (!isNullableTimestamp(value.auto_export_completed_at)) return false;
  if (!isAutoExportFormats(value.auto_export_formats)) return false;
  if (hasOwn(value, 'archived_at') && !isNullableTimestamp(value.archived_at)) return false;
  if (!isNullableString(value.raw_results_file) || !isNullableString(value.mesh_artifact_file)) return false;
  if (!isStringArray(value.log_tail)) return false;
  if (hasOwn(value, 'results_discarded_at') && !isNullableTimestamp(value.results_discarded_at)) return false;
  if (hasOwn(value, 'mesh_discarded_at') && !isNullableTimestamp(value.mesh_discarded_at)) return false;
  if (hasOwn(value, 'solve_path') && !(
    value.solve_path === null || value.solve_path === 'full-3d' || value.solve_path === 'axisymmetric-meridian'
  )) return false;
  if (hasOwn(value, 'axisymmetric_eligibility_reasons') && !isStringArray(value.axisymmetric_eligibility_reasons)) return false;
  if (hasOwn(value, 'solve_wall_time_seconds') && !(
    value.solve_wall_time_seconds === null
    || (isFiniteNumber(value.solve_wall_time_seconds) && value.solve_wall_time_seconds >= 0)
  )) return false;
  if (hasOwn(value, 'cad_source') && !(value.cad_source === null || isCadSource(value.cad_source))) return false;
  return true;
}

function isJobResults(value: unknown, depth = 0): value is ResultData {
  if (depth >= 8 || !isRecord(value) || !isSafeJson(value)) return false;
  if (!isNumberArray(value.frequencies)) return false;
  for (const blockName of ['spl_on_axis', 'impedance'] as const) {
    if (!hasOwn(value, blockName)) continue;
    const block = value[blockName];
    if (!isRecord(block)) return false;
    for (const key of ['frequencies', 'spl', 'phase_degrees', 'real', 'imaginary']) {
      if (hasOwn(block, key) && !isNumberArray(block[key], true)) return false;
    }
  }
  if (hasOwn(value, 'di')) {
    const block = value.di;
    if (!isRecord(block)) return false;
    if (hasOwn(block, 'frequencies') && !isNumberArray(block.frequencies)) return false;
    if (hasOwn(block, 'di')) {
      const values = block.di;
      if (!isNumberArray(values, true) && !(
        isRecord(values) && Object.values(values).every((series) => isNumberArray(series, true))
      )) return false;
    }
  }
  for (const blockName of ['directivity', 'directivity_phase'] as const) {
    if (!hasOwn(value, blockName)) continue;
    const block = value[blockName];
    if (!isRecord(block)) return false;
    const validSample = (sample: unknown): boolean => (
      Array.isArray(sample)
      && sample.length === 2
      && isFiniteNumber(sample[0])
      && (isNullableNumber(sample[1]) || (
        Array.isArray(sample[1])
        && sample[1].length === 2
        && sample[1].every(isFiniteNumber)
      ))
    );
    if (!Object.values(block).every((rows) => (
      Array.isArray(rows)
      && rows.every((row) => Array.isArray(row) && row.every(validSample))
    ))) return false;
  }
  if (hasOwn(value, 'metadata') && !isRecord(value.metadata)) return false;
  if (hasOwn(value, 'balloon')) {
    const balloon = value.balloon;
    if (!isRecord(balloon)) return false;
    if (!isNumberArray(balloon.frequencies) || !isNumberArray(balloon.theta_deg) || !isNumberArray(balloon.phi_deg)) return false;
    if (!Array.isArray(balloon.spl_norm_db) || !balloon.spl_norm_db.every((grid) => (
      Array.isArray(grid)
      && grid.every((row) => isNumberArray(row, true))
    ))) return false;
    if (hasOwn(balloon, 'distance_m') && !isFiniteNumber(balloon.distance_m)) return false;
    if (hasOwn(balloon, 'hemisphere') && typeof balloon.hemisphere !== 'boolean') return false;
  }
  if (hasOwn(value, 'beam_shape')) {
    const beam = value.beam_shape;
    if (!isRecord(beam)) return false;
    for (const key of [
      'frequencies', 'shape_exponent', 'fit_residual_percent',
      'horizontal_beamwidth_deg', 'vertical_beamwidth_deg', 'aspect_ratio',
      'spherical_di_db',
    ]) {
      if (hasOwn(beam, key) && !isNumberArray(beam[key], true)) return false;
    }
    if (hasOwn(beam, 'valid') && !(
      Array.isArray(beam.valid) && beam.valid.every((item) => typeof item === 'boolean')
    )) return false;
    if (hasOwn(beam, 'level_db') && !isFiniteNumber(beam.level_db)) return false;
    for (const key of ['di_domain', 'di_sampling_domain']) {
      if (hasOwn(beam, key) && typeof beam[key] !== 'string') return false;
    }
  }
  if (hasOwn(value, 'channel_order') && !isStringArray(value.channel_order)) return false;
  if (hasOwn(value, 'channels')) {
    if (!isRecord(value.channels)) return false;
    if (!Object.values(value.channels).every((result) => isJobResults(result, depth + 1))) return false;
  }
  return true;
}

function optionalEpochIsValid(message: JsonRecord): boolean {
  return !hasOwn(message, 'epoch') || isNonNegativeInteger(message.epoch);
}

function parseHello(message: JsonRecord): HelloMessage | null {
  if (!isNonNegativeInteger(message.epoch)) return null;
  if (!isFiniteNumber(message.heartbeatSec) || message.heartbeatSec <= 0) return null;
  return message as unknown as HelloMessage;
}

function parseSnapshot(message: JsonRecord): SnapshotMessage | null {
  if (!optionalEpochIsValid(message) || !isNonNegativeInteger(message.cursor) || !Array.isArray(message.jobs)) return null;
  if (!message.jobs.every(isJobItem)) return null;
  const ids = new Set(message.jobs.map((job) => job.id));
  if (ids.size !== message.jobs.length) return null;
  return message as unknown as SnapshotMessage;
}

function sanitizeMetadataChanges(value: unknown): Partial<JobItem> & JsonRecord | null {
  if (!isRecord(value)) return null;
  const patch: Partial<JobItem> & JsonRecord = {};
  if (hasOwn(value, 'label')) {
    if (!isNullableString(value.label)) return null;
    patch.label = value.label;
  }
  if (hasOwn(value, 'rating')) {
    if (!(value.rating === null || (Number.isSafeInteger(value.rating) && Number(value.rating) >= 0 && Number(value.rating) <= 5))) return null;
    patch.rating = value.rating as number | null;
  }
  if (hasOwn(value, 'exported_files')) {
    if (!isStringArray(value.exported_files)) return null;
    patch.exported_files = value.exported_files;
  }
  if (hasOwn(value, 'auto_export_completed_at')) {
    if (!isNullableTimestamp(value.auto_export_completed_at)) return null;
    patch.auto_export_completed_at = value.auto_export_completed_at;
  }
  if (hasOwn(value, 'archived_at')) {
    if (!isNullableTimestamp(value.archived_at)) return null;
    patch.archived_at = value.archived_at;
  }
  if (hasOwn(value, 'auto_export_formats')) {
    if (!isAutoExportFormats(value.auto_export_formats)) return null;
    patch.auto_export_formats = value.auto_export_formats;
  }
  for (const key of ['raw_results_file', 'mesh_artifact_file'] as const) {
    if (!hasOwn(value, key)) continue;
    if (!isNullableString(value[key])) return null;
    patch[key] = value[key];
  }
  for (const key of ['results_discarded_at', 'mesh_discarded_at'] as const) {
    if (!hasOwn(value, key)) continue;
    if (!isNullableTimestamp(value[key])) return null;
    patch[key] = value[key];
  }
  if (hasOwn(value, 'script_snapshot')) {
    if (!(value.script_snapshot === null || (isRecord(value.script_snapshot) && isSafeJson(value.script_snapshot)))) return null;
    patch.script_snapshot = value.script_snapshot;
  }
  for (const key of ['has_results', 'has_mesh_artifact'] as const) {
    if (!hasOwn(value, key)) continue;
    if (typeof value[key] !== 'boolean') return null;
    patch[key] = value[key];
  }
  if (hasOwn(value, 'has_pressure_basis_artifact')) {
    if (typeof value.has_pressure_basis_artifact !== 'boolean') return null;
    patch.has_pressure_basis_artifact = value.has_pressure_basis_artifact;
  }
  if (hasOwn(value, 'pressure_basis_artifact_bytes')) {
    if (!(value.pressure_basis_artifact_bytes === null || isNonNegativeInteger(value.pressure_basis_artifact_bytes))) return null;
    patch.pressure_basis_artifact_bytes = value.pressure_basis_artifact_bytes as number | null;
  }
  if (hasOwn(value, 'field_plane_available')) {
    if (typeof value.field_plane_available !== 'boolean') return null;
    patch.field_plane_available = value.field_plane_available;
  }
  if (hasOwn(value, 'field_trace_bytes')) {
    if (!(value.field_trace_bytes === null || isNonNegativeInteger(value.field_trace_bytes))) return null;
    patch.field_trace_bytes = value.field_trace_bytes as number | null;
  }
  if (hasOwn(value, 'unavailable_reason')) {
    if (!isNullableString(value.unavailable_reason)) return null;
    patch.unavailable_reason = value.unavailable_reason;
  }
  // Retention metadata delivered through this event type: the radiation
  // artifact appears when the campaign finishes and disappears when retention
  // cleans it up, and the run card's download action follows it either way.
  if (hasOwn(value, 'has_radiation_impedance_artifact')) {
    if (typeof value.has_radiation_impedance_artifact !== 'boolean') return null;
    patch.has_radiation_impedance_artifact = value.has_radiation_impedance_artifact;
  }
  if (hasOwn(value, 'radiation_impedance_artifact_bytes')) {
    if (!(value.radiation_impedance_artifact_bytes === null || isNonNegativeInteger(value.radiation_impedance_artifact_bytes))) return null;
    patch.radiation_impedance_artifact_bytes = value.radiation_impedance_artifact_bytes as number | null;
  }
  if (hasOwn(value, 'persistence_warnings')) {
    if (!isStringArray(value.persistence_warnings)) return null;
    patch.persistence_warnings = value.persistence_warnings;
  }
  return patch;
}

function parseEvent(message: JsonRecord): EventMessage | { unknownType: string; cursor: number; jobId: string } | null {
  if (!optionalEpochIsValid(message) || !isNonNegativeInteger(message.cursor)) return null;
  if (typeof message.jobId !== 'string' || message.jobId.length === 0 || typeof message.type !== 'string') return null;
  if (hasOwn(message, 'payload') && !isRecord(message.payload)) return null;
  if (!EVENT_TYPES.has(message.type as EventType)) {
    return { unknownType: message.type, cursor: message.cursor, jobId: message.jobId };
  }
  const source = (message.payload ?? {}) as JsonRecord;
  const payload: JsonRecord = {};
  if (message.type === 'started' && hasOwn(source, 'started_at')) {
    if (!isTimestamp(source.started_at)) return null;
    payload.started_at = source.started_at;
  }
  if (message.type === 'progress') {
    if (!isFiniteNumber(source.progress) || source.progress < 0 || source.progress > 1) return null;
    payload.progress = source.progress;
  }
  if (message.type === 'stage') {
    if (hasOwn(source, 'stage')) {
      if (!isNullableString(source.stage)) return null;
      payload.stage = source.stage;
    }
    if (hasOwn(source, 'message')) {
      if (!isNullableString(source.message)) return null;
      payload.message = source.message;
    }
    if (hasOwn(source, 'progress')) {
      if (!isFiniteNumber(source.progress) || source.progress < 0 || source.progress > 1) return null;
      payload.progress = source.progress;
    }
  }
  if (message.type === 'log') {
    if (hasOwn(source, 'chunk')) {
      if (typeof source.chunk !== 'string') return null;
      payload.chunk = source.chunk;
    }
    if (hasOwn(source, 'lines')) {
      if (!isStringArray(source.lines)) return null;
      payload.lines = source.lines;
    }
  }
  if ((message.type === 'failed' || message.type === 'cancelled') && hasOwn(source, 'message')) {
    if (typeof source.message !== 'string') return null;
    payload.message = source.message;
  }
  if (message.type === 'metadata' && hasOwn(source, 'changed')) {
    const changed = sanitizeMetadataChanges(source.changed);
    if (changed === null) return null;
    payload.changed = changed;
  }
  return { ...message, type: message.type as EventType, payload } as unknown as EventMessage;
}

function parsePartialResult(message: JsonRecord): PartialResultMessage | null {
  if (!optionalEpochIsValid(message)) return null;
  if (typeof message.jobId !== 'string' || message.jobId.length === 0) return null;
  if (!Number.isSafeInteger(message.revision) || Number(message.revision) < 1) return null;
  if (hasOwn(message, 'snapshot') && typeof message.snapshot !== 'boolean') return null;
  if (!isJobResults(message.result)) return null;
  const result = message.result as ResultData & JsonRecord;
  const multiChannelShape = hasOwn(result, 'channels')
    || hasOwn(result, 'channel_order')
    || result.result_kind === 'multi_channel';
  if (multiChannelShape && !(
    result.result_kind === 'multi_channel'
    && result.result_contract_version === 2
    && isRecord(result.channels)
    && isStringArray(result.channel_order)
  )) return null;
  return message as unknown as PartialResultMessage;
}

const OPEN = 1;
const MAX_JOB_REFRESH_ATTEMPTS = 3;
const defaultFactory: JobsWebSocketFactory = (url) => new WebSocket(url) as unknown as JobsWebSocketLike;
const defaultFetch: JobsFetch = (input, init) => fetch(input, init);

function jobsUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost/ws/jobs';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/jobs`;
}

async function responseError(response: Response): Promise<Error> {
  let detail = `${response.status} ${response.statusText}`.trim();
  try {
    const body = await response.json() as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch {
    // A non-JSON response still has a useful status.
  }
  return new Error(detail);
}

export class JobsSocketManager {
  private socket: JobsWebSocketLike | null = null;
  private stopped = true;
  private helloSeen = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatMs = 30_000;
  private refetchGeneration = 0;
  private jobMutationCounter = 0;
  private readonly jobMutationVersions = new Map<string, number>();
  private gapTargetCursor: number | null = null;
  private readonly jobGenerations = new Map<string, number>();
  private readonly jobRefreshes = new Map<string, Promise<void>>();
  private readonly partialRefreshes = new Map<string, Promise<void>>();
  private readonly ratingMutations = new Map<string, {
    tail: Promise<void>;
    token: number;
    confirmed: number | null;
  }>();
  private readonly listeners = new Set<() => void>();
  private snapshot: JobsSnapshot = {
    connection: 'idle', epoch: null, cursor: null, jobs: [], error: null,
  };

  constructor(
    private readonly factory: JobsWebSocketFactory = defaultFactory,
    private readonly fetcher: JobsFetch = defaultFetch,
    private readonly url: string = jobsUrl(),
  ) {}

  getSnapshot = (): JobsSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect(false);
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    provisionalResults.clear();
    this.update({ connection: 'disconnected', epoch: null });
  }

  async refresh(): Promise<void> {
    await this.refetchJobs();
  }

  async stopJob(jobId: string): Promise<void> {
    const response = await this.fetcher(`/api/stop/${encodeURIComponent(jobId)}`, { method: 'POST' });
    if (!response.ok) throw await responseError(response);
    await this.refreshJob(jobId);
  }

  async retryJob(jobId: string): Promise<void> {
    const response = await this.fetcher(
      `/api/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: 'POST' },
    );
    if (!response.ok) throw await responseError(response);
    await this.refetchJobs();
  }

  async clearFailed(): Promise<void> {
    const response = await this.fetcher('/api/jobs/clear-failed', { method: 'DELETE' });
    if (!response.ok) throw await responseError(response);
    const body = await response.json() as { deleted_ids?: string[] };
    const deleted = new Set(body.deleted_ids ?? []);
    deleted.forEach((jobId) => {
      this.invalidateJob(jobId);
      provisionalResults.remove(jobId);
      this.markJobMutation(jobId);
    });
    this.update({ jobs: this.snapshot.jobs.filter((job) => !deleted.has(job.id)) });
  }

  async deleteJob(jobId: string): Promise<void> {
    const response = await this.fetcher(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
    if (!response.ok) throw await responseError(response);
    this.invalidateJob(jobId);
    provisionalResults.remove(jobId);
    this.markJobMutation(jobId);
    this.update({ jobs: this.snapshot.jobs.filter((job) => job.id !== jobId) });
  }

  async patchMetadata(jobId: string, fields: Record<string, unknown>): Promise<void> {
    const response = await this.fetcher(`/api/jobs/${encodeURIComponent(jobId)}/metadata`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    });
    if (!response.ok) throw await responseError(response);
  }

  async patchRating(jobId: string, rating: number | null): Promise<void> {
    const previous = this.snapshot.jobs.find((job) => job.id === jobId)?.rating ?? null;
    const state = this.ratingMutations.get(jobId) ?? {
      tail: Promise.resolve(), token: 0, confirmed: previous,
    };
    const token = state.token + 1;
    state.token = token;
    this.patchJob(jobId, { rating });
    const mutation = state.tail.catch(() => undefined).then(async () => {
      try {
        await this.patchMetadata(jobId, { rating });
        state.confirmed = rating;
      } catch (error) {
        const current = this.snapshot.jobs.find((job) => job.id === jobId)?.rating ?? null;
        if (state.token === token && current === rating) this.patchJob(jobId, { rating: state.confirmed });
        throw error;
      }
    });
    state.tail = mutation;
    this.ratingMutations.set(jobId, state);
    try {
      await mutation;
    } finally {
      if (this.ratingMutations.get(jobId)?.tail === mutation) this.ratingMutations.delete(jobId);
    }
  }

  private connect(reconnecting: boolean): void {
    if (this.stopped) return;
    this.update({ connection: reconnecting ? 'reconnecting' : 'connecting', epoch: null, error: null });
    const socket = this.factory(this.url);
    this.socket = socket;
    this.helloSeen = false;
    socket.onopen = () => undefined;
    socket.onmessage = (event) => this.onMessage(socket, event.data);
    socket.onerror = () => this.update({ error: 'Jobs connection error' });
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  private onMessage(socket: JobsWebSocketLike, raw: unknown): void {
    if (socket !== this.socket || typeof raw !== 'string') return;
    let decoded: unknown;
    try {
      decoded = JSON.parse(raw) as unknown;
    } catch {
      this.update({ error: 'Malformed jobs message' });
      return;
    }
    if (!isRecord(decoded)) {
      this.update({ error: 'Malformed jobs message' });
      return;
    }
    if (decoded.v !== 1) {
      this.update({ error: 'Unsupported jobs protocol message' });
      return;
    }
    if (typeof decoded.kind !== 'string') {
      this.update({ error: 'Malformed jobs message' });
      return;
    }
    if (decoded.kind === 'hello') {
      const hello = parseHello(decoded);
      if (hello === null) {
        this.update({ error: 'Invalid jobs hello message' });
        return;
      }
      if (this.helloSeen) return;
      this.helloSeen = true;
      this.reconnectAttempt = 0;
      this.heartbeatMs = Math.max(250, hello.heartbeatSec * 2_000);
      this.update({ connection: 'connected', epoch: hello.epoch, error: null });
      this.armHeartbeat();
      if (this.snapshot.cursor !== null && socket.readyState === OPEN) {
        socket.send(JSON.stringify({ v: 1, kind: 'resume', epoch: hello.epoch, cursor: this.snapshot.cursor }));
      }
      return;
    }
    if (!this.helloSeen) return;
    if (!optionalEpochIsValid(decoded)) {
      this.update({ error: 'Invalid jobs message epoch' });
      return;
    }
    if (hasOwn(decoded, 'epoch') && decoded.epoch !== this.snapshot.epoch) return;
    if (decoded.kind === 'partialResult') {
      const partial = parsePartialResult(decoded);
      if (partial === null) {
        this.update({ error: 'Invalid jobs partialResult message' });
        return;
      }
      this.armHeartbeat();
      const applied = provisionalResults.apply(
        partial.jobId,
        partial.revision,
        partial.result,
        partial.snapshot === true,
      );
      if (!applied) void this.refreshPartialResults(partial.jobId);
      this.update({ error: null });
      return;
    }
    if (decoded.kind === 'snapshot') {
      const incoming = parseSnapshot(decoded);
      if (incoming === null) {
        this.update({ error: 'Invalid jobs snapshot message' });
        return;
      }
      this.armHeartbeat();
      this.gapTargetCursor = null;
      new Set([...this.snapshot.jobs.map((job) => job.id), ...incoming.jobs.map((job) => job.id)])
        .forEach((jobId) => this.markJobMutation(jobId));
      provisionalResults.prune(new Set(incoming.jobs
        .filter((job) => job.status === 'running' || job.status === 'queued')
        .map((job) => job.id)));
      this.update({ cursor: incoming.cursor, jobs: this.sortJobs(incoming.jobs), error: null });
      return;
    }
    if (decoded.kind === 'event') {
      const event = parseEvent(decoded);
      if (event === null) {
        if (isNonNegativeInteger(decoded.cursor)) {
          this.armHeartbeat();
          this.onUnusableEvent({
            cursor: decoded.cursor,
            error: 'Invalid jobs event message; resyncing',
          });
          return;
        }
        this.update({ error: 'Invalid jobs event message' });
        return;
      }
      this.armHeartbeat();
      if ('unknownType' in event) {
        this.onUnusableEvent({
          cursor: event.cursor,
          error: `Unsupported jobs event type "${event.unknownType}"; resyncing`,
        });
        return;
      }
      this.onEvent(event);
      return;
    }
    // Additive message kinds are ignored until this client understands them.
    // They still prove the connection is alive.
    this.armHeartbeat();
  }

  private onUnusableEvent(message: { cursor: number; error: string }): void {
    const cursor = this.snapshot.cursor;
    if (cursor !== null && message.cursor <= cursor) return;
    if (cursor !== null && message.cursor !== cursor + 1) {
      this.gapTargetCursor = message.cursor;
      this.update({ error: `Jobs event gap (${cursor} → ${message.cursor}); resyncing` });
      if (this.socket?.readyState === OPEN && this.snapshot.epoch !== null) {
        this.socket.send(JSON.stringify({
          v: 1,
          kind: 'resume',
          epoch: this.snapshot.epoch,
          cursor,
        }));
      }
      return;
    }
    // The cursor is real, but this client cannot safely infer the event's
    // effect. Advance past it and rebuild durable rows through the HTTP source
    // of truth so an additive server event cannot wedge replay forever.
    this.gapTargetCursor = null;
    this.update({
      cursor: message.cursor,
      error: message.error,
    });
    void this.refetchJobs();
  }

  private onEvent(message: EventMessage): void {
    const cursor = this.snapshot.cursor;
    if (cursor !== null && message.cursor <= cursor) return;
    if (this.gapTargetCursor !== null) {
      if (cursor === null || message.cursor !== cursor + 1) return;
      this.markJobMutation(message.jobId);
      if (message.type === 'deleted') {
        this.invalidateJob(message.jobId);
        provisionalResults.remove(message.jobId);
        this.update({
          cursor: message.cursor,
          jobs: this.snapshot.jobs.filter((job) => job.id !== message.jobId),
          error: this.snapshot.error,
        });
      } else {
        this.commitEvent(message);
        if (this.eventNeedsRefresh(message)) void this.refreshJob(message.jobId);
      }
      if (message.cursor >= this.gapTargetCursor) {
        this.gapTargetCursor = null;
        this.update({ error: null });
      }
      return;
    }
    if (cursor !== null && message.cursor !== cursor + 1) {
      this.gapTargetCursor = message.cursor;
      this.update({ error: `Jobs event gap (${cursor} → ${message.cursor}); resyncing` });
      if (this.socket?.readyState === OPEN && this.snapshot.epoch !== null) {
        this.socket.send(JSON.stringify({
          v: 1,
          kind: 'resume',
          epoch: this.snapshot.epoch,
          cursor,
        }));
      }
      return;
    }
    this.markJobMutation(message.jobId);
    if (message.type === 'deleted') {
      this.invalidateJob(message.jobId);
      provisionalResults.remove(message.jobId);
      this.update({
        cursor: message.cursor,
        jobs: this.snapshot.jobs.filter((job) => job.id !== message.jobId),
        error: null,
      });
      return;
    }
    this.commitEvent(message);
    if (this.eventNeedsRefresh(message)) void this.refreshJob(message.jobId);
  }

  /**
   * Advance the cursor and apply the event's effect in one update.
   *
   * Two updates would mean two notifications, and the cursor always moves --
   * so subscribers were woken for every event whether or not anything they
   * render had changed. The cursor is internal resume bookkeeping; nothing in
   * the interface displays it.
   */
  private commitEvent(message: EventMessage): void {
    const jobs = this.applyDelta(message);
    const error = this.gapTargetCursor === null ? null : this.snapshot.error;
    this.update(jobs === null
      ? { cursor: message.cursor, error }
      : { cursor: message.cursor, jobs, error });
  }

  private eventNeedsRefresh(message: EventMessage): boolean {
    if (!this.snapshot.jobs.some((job) => job.id === message.jobId)) return true;
    return message.type === 'queued'
      || message.type === 'completed'
      || message.type === 'failed'
      || message.type === 'cancelled';
  }

  /** The job list this event implies, or null when it changes nothing. */
  private applyDelta(message: EventMessage): JobItem[] | null {
    const payload = message.payload ?? {};
    const patch: Partial<JobItem> = {};
    if (message.type === 'queued') Object.assign(patch, { status: 'queued', progress: 0 });
    if (message.type === 'started') Object.assign(patch, { status: 'running', started_at: String(payload.started_at ?? new Date().toISOString()) });
    if (message.type === 'progress') patch.progress = Number(payload.progress ?? 0);
    if (message.type === 'stage') Object.assign(patch, {
      stage: typeof payload.stage === 'string' ? payload.stage : null,
      stage_message: typeof payload.message === 'string' ? payload.message : null,
      ...(typeof payload.progress === 'number' ? { progress: payload.progress } : {}),
    });
    if (message.type === 'log') {
      const chunk = typeof payload.chunk === 'string' ? payload.chunk : '';
      const current = this.snapshot.jobs.find((job) => job.id === message.jobId)?.log_tail ?? [];
      const lines = Array.isArray(payload.lines)
        ? payload.lines.filter((line): line is string => typeof line === 'string' && Boolean(line))
        : chunk.split(/\r\n|\r|\n/).filter(Boolean);
      patch.log_tail = [...current, ...lines].slice(-30);
    }
    if (message.type === 'completed') Object.assign(patch, { status: 'complete', progress: 1, has_results: true });
    if (message.type === 'failed') Object.assign(patch, { status: 'error', error_message: String(payload.message ?? 'Simulation failed') });
    if (message.type === 'cancelled') Object.assign(patch, { status: 'cancelled', error_message: String(payload.message ?? 'Simulation cancelled') });
    if (message.type === 'failed' || message.type === 'cancelled') provisionalResults.remove(message.jobId);
    if (message.type === 'metadata' && isRecord(payload.changed)) {
      // parseEvent has already copied only the metadata fields that the jobs
      // protocol permits. Never merge the raw server object into a job row.
      Object.assign(patch, payload.changed as Partial<JobItem>);
    }
    return this.patchedJobs(message.jobId, patch);
  }

  private async refetchJobs(): Promise<void> {
    const generation = ++this.refetchGeneration;
    const mutationBaseline = this.jobMutationCounter;
    try {
      const pageSize = 200;
      const fetched = new Map<string, JobItem>();
      let offset = 0;
      let total = Infinity;
      while (offset < total) {
        const response = await this.fetcher(`/api/jobs?limit=${pageSize}&offset=${offset}`);
        if (!response.ok) throw await responseError(response);
        const body = await response.json() as unknown;
        if (!isRecord(body) || !Array.isArray(body.items) || !body.items.every(isJobItem)) {
          throw new Error('Invalid jobs list response');
        }
        if (hasOwn(body, 'total') && !isNonNegativeInteger(body.total)) {
          throw new Error('Invalid jobs list total');
        }
        if (generation !== this.refetchGeneration) return;
        body.items.forEach((job) => fetched.set(job.id, job));
        offset += body.items.length;
        total = isNonNegativeInteger(body.total) ? body.total : offset;
        // A changing database can legitimately make the final page shorter
        // than the count observed on an earlier page. Never spin on an empty
        // page while trying to reach a now-stale total.
        if (body.items.length === 0) break;
      }
      // REST pages are not one atomic server snapshot. Preserve current
      // membership when a mutation could have shifted an offset boundary, then
      // overlay each job changed through WS/status/local traffic. A changed id
      // absent from the current list represents a concurrent deletion and must
      // not be resurrected by an older page.
      const current = new Map(this.snapshot.jobs.map((job) => [job.id, job]));
      if (this.jobMutationCounter !== mutationBaseline) {
        // Insertions/deletions shift offset pagination. If anything changed
        // during the walk, a still-live row missing from every fetched page may
        // simply have crossed a page boundary. Preserve those current rows;
        // per-id tombstones below still remove jobs actually deleted.
        current.forEach((job, jobId) => {
          if (!fetched.has(jobId)) fetched.set(jobId, job);
        });
      }
      this.jobMutationVersions.forEach((version, jobId) => {
        if (version <= mutationBaseline) return;
        const live = current.get(jobId);
        if (live) fetched.set(jobId, live);
        else fetched.delete(jobId);
      });
      const items = [...fetched.values()];
      new Set([...this.snapshot.jobs.map((job) => job.id), ...items.map((job) => job.id)])
        .forEach((jobId) => this.invalidateJob(jobId));
      this.update({ jobs: this.sortJobs(items), error: this.gapTargetCursor === null ? null : this.snapshot.error });
    } catch (error) {
      if (generation !== this.refetchGeneration) return;
      this.update({ error: error instanceof Error ? error.message : String(error) });
    }
  }

  private refreshJob(jobId: string): Promise<void> {
    const existing = this.jobRefreshes.get(jobId);
    if (existing) return existing;

    const generation = this.nextJobGeneration(jobId);
    const refresh = this.refreshJobUntilStable(jobId, generation);
    this.jobRefreshes.set(jobId, refresh);
    const clearRefresh = () => {
      if (this.jobRefreshes.get(jobId) === refresh) this.jobRefreshes.delete(jobId);
    };
    void refresh.then(clearRefresh, clearRefresh);
    return refresh;
  }

  private refreshPartialResults(jobId: string): Promise<void> {
    const existing = this.partialRefreshes.get(jobId);
    if (existing) return existing;
    const refresh = (async () => {
      try {
        const response = await this.fetcher(`/api/partial-results/${encodeURIComponent(jobId)}`);
        if (response.status === 404) {
          provisionalResults.remove(jobId);
          return;
        }
        if (!response.ok) throw await responseError(response);
        const body = await response.json() as unknown;
        if (
          isRecord(body)
          && Number.isSafeInteger(body.revision)
          && Number(body.revision) >= 1
          && isJobResults(body.result)
        ) {
          provisionalResults.apply(jobId, Number(body.revision), body.result, true);
        } else {
          throw new Error('Invalid partial results response');
        }
      } catch (error) {
        this.update({ error: error instanceof Error ? error.message : String(error) });
      }
    })();
    this.partialRefreshes.set(jobId, refresh);
    const clear = () => {
      if (this.partialRefreshes.get(jobId) === refresh) this.partialRefreshes.delete(jobId);
    };
    void refresh.then(clear, clear);
    return refresh;
  }

  private async refreshJobUntilStable(jobId: string, generation: number): Promise<void> {
    for (let attempt = 0; attempt < MAX_JOB_REFRESH_ATTEMPTS; attempt += 1) {
      const mutationBaseline = this.jobMutationVersions.get(jobId) ?? 0;
      try {
        const response = await this.fetcher(`/api/status/${encodeURIComponent(jobId)}`);
        if (this.jobGenerations.get(jobId) !== generation) return;
        if ((this.jobMutationVersions.get(jobId) ?? 0) > mutationBaseline) continue;
        if (response.status === 404) {
          this.invalidateJob(jobId);
          this.markJobMutation(jobId);
          this.update({ jobs: this.snapshot.jobs.filter((job) => job.id !== jobId) });
          return;
        }
        if (!response.ok) throw await responseError(response);
        const job = await response.json() as unknown;
        if (!isJobItem(job) || job.id !== jobId) throw new Error('Invalid job status response');
        if (this.jobGenerations.get(jobId) !== generation) return;
        if ((this.jobMutationVersions.get(jobId) ?? 0) > mutationBaseline) continue;
        this.markJobMutation(jobId);
        const jobs = this.snapshot.jobs.filter((item) => item.id !== job.id);
        this.update({ jobs: this.sortJobs([...jobs, job]), error: null });
        return;
      } catch (error) {
        if (this.jobGenerations.get(jobId) !== generation) return;
        this.update({ error: error instanceof Error ? error.message : String(error) });
        return;
      }
    }
  }

  private nextJobGeneration(jobId: string): number {
    const generation = (this.jobGenerations.get(jobId) ?? 0) + 1;
    this.jobGenerations.set(jobId, generation);
    return generation;
  }

  private invalidateJob(jobId: string): void {
    this.nextJobGeneration(jobId);
  }

  private markJobMutation(jobId: string): void {
    this.jobMutationCounter += 1;
    this.jobMutationVersions.set(jobId, this.jobMutationCounter);
  }

  /** Apply a patch immediately, for local optimistic edits such as a rating. */
  private patchJob(jobId: string, patch: Partial<JobItem>): void {
    this.markJobMutation(jobId);
    const jobs = this.patchedJobs(jobId, patch);
    if (jobs !== null) this.update({ jobs });
  }

  /** Merge a patch into one job, returning null when nothing actually changes. */
  private patchedJobs(jobId: string, patch: Partial<JobItem>): JobItem[] | null {
    let changed = false;
    const jobs = this.snapshot.jobs.map((job) => {
      if (job.id !== jobId) return job;
      const merged = { ...job, ...patch };
      // A running solve emits progress, stage and log events continuously, and
      // many carry values the list already holds. Returning the same object
      // keeps the snapshot identity stable so nothing downstream re-renders.
      if (shallowEqual(job, merged)) return job;
      changed = true;
      return merged;
    });
    // No re-sort: created_at never changes, so a patch cannot reorder the list.
    return changed ? jobs : null;
  }

  private sortJobs(jobs: JobItem[]): JobItem[] {
    // Only for calls that change membership. Date.parse is called once per job
    // rather than twice per comparison, which matters at the 200-job page size.
    return jobs
      .map((job) => ({ job, at: Date.parse(job.created_at) }))
      .sort((a, b) => b.at - a.at || b.job.run_number - a.job.run_number)
      .map((entry) => entry.job);
  }

  private scheduleReconnect(): void {
    this.update({ connection: 'reconnecting', epoch: null });
    const delay = Math.min(5_000, 250 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect(true);
    }, delay);
  }

  private armHeartbeat(): void {
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => this.socket?.close(), this.heartbeatMs);
  }

  private clearTimers(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
  }

  private update(patch: Partial<JobsSnapshot>): void {
    const previous = this.snapshot;
    const next = { ...previous, ...patch };
    this.snapshot = next;
    if (patch.jobs) compareSelection.prune(new Set(patch.jobs.map((job) => job.id)));
    // Every subscriber reads the whole snapshot through useSyncExternalStore,
    // which compares by identity -- so any notification re-renders the Results
    // panel, the Jobs panel and the coordinator. Two things must therefore not
    // wake them: an update that changed nothing at all, and the event cursor,
    // which advances on every single event of a running solve but is internal
    // resume bookkeeping that no part of the interface displays. The value is
    // still written above, so a render triggered by anything else sees it.
    if (shallowEqual({ ...previous, cursor: next.cursor }, next)) return;
    this.listeners.forEach((listener) => listener());
  }
}

export const jobsSocket = new JobsSocketManager();
