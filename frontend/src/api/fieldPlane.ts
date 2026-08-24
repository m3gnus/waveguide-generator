export const FIELD_PLANE_VERSION = 1 as const;
export const FIELD_PLANE_HEADER_VERSION = 2 as const;
export const FIELD_PLANE_ORDERING = 'v-major-row-major' as const;

export type Vector3Tuple = [number, number, number];
/** 'channel:<id>' is the raw retained basis; 'member:<id>' is that channel
 * with its stored crossover combine weight (filter/gain/delay/polarity)
 * applied, i.e. the channel as it plays inside the system sum. */
export type FieldPlaneResponseId = 'system' | `channel:${string}` | `member:${string}`;

export interface FieldPlaneSpec {
  origin_m: Vector3Tuple;
  axis_u: Vector3Tuple;
  axis_v: Vector3Tuple;
  width_m: number;
  height_m: number;
  nx: number;
  ny: number;
}

export interface FieldPlaneRequest {
  version: typeof FIELD_PLANE_VERSION;
  request_id: string;
  plane: FieldPlaneSpec;
  frequency_index: number;
  response: { id: FieldPlaneResponseId };
}

export interface FieldPlaneHeader {
  version: typeof FIELD_PLANE_VERSION | typeof FIELD_PLANE_HEADER_VERSION;
  request_id: string;
  job_id: string;
  frequency_index: number;
  frequency_hz: number;
  nx: number;
  ny: number;
  ordering: typeof FIELD_PLANE_ORDERING;
  phase_convention: string;
  pressure_unit: string;
  response_id: string;
  geometry_sha256: string;
  synthesis_revision: string | null;
  symmetry_plane: string | null;
}

export interface DecodedFieldPlane {
  header: FieldPlaneHeader;
  real: Float32Array;
  imag: Float32Array;
}

export interface BuildFieldPlaneRequestOptions {
  requestId: string;
  plane: FieldPlaneSpec;
  frequencyIndex: number;
  responseId?: FieldPlaneResponseId;
}

function validateRequestPlane(plane: FieldPlaneSpec): FieldPlaneSpec {
  const vectors = [plane.origin_m, plane.axis_u, plane.axis_v];
  if (vectors.some((vector) => vector.length !== 3 || vector.some((value) => !Number.isFinite(value)))) {
    throw new Error('Field-plane vectors must contain three finite numbers');
  }
  if (!(plane.width_m > 0 && plane.width_m <= 100) || !(plane.height_m > 0 && plane.height_m <= 100)) {
    throw new Error('Field-plane extents must be finite and in (0, 100] metres');
  }
  if (
    !Number.isSafeInteger(plane.nx) || plane.nx < 2 || plane.nx > 256
    || !Number.isSafeInteger(plane.ny) || plane.ny < 2 || plane.ny > 256
    || plane.nx * plane.ny > 65_536
  ) throw new Error('Field-plane grid must be 2–256 per axis and contain at most 65536 points');
  const norm = (axis: Vector3Tuple) => Math.hypot(...axis);
  const dot = plane.axis_u.reduce((sum, value, index) => sum + value * plane.axis_v[index], 0);
  if (Math.abs(norm(plane.axis_u) - 1) > 1e-6 || Math.abs(norm(plane.axis_v) - 1) > 1e-6 || Math.abs(dot) > 1e-6) {
    throw new Error('Field-plane axes must be unit length and orthogonal');
  }
  return {
    ...plane,
    origin_m: [...plane.origin_m],
    axis_u: [...plane.axis_u],
    axis_v: [...plane.axis_v],
  };
}

export function buildFieldPlaneRequest({
  requestId,
  plane,
  frequencyIndex,
  responseId = 'system',
}: BuildFieldPlaneRequestOptions): FieldPlaneRequest {
  const normalizedRequestId = requestId.trim();
  const normalizedResponseId = responseId.trim() as FieldPlaneResponseId;
  if (!normalizedRequestId || normalizedRequestId.length > 256) {
    throw new Error('Field-plane request id must contain 1–256 characters');
  }
  if (!Number.isSafeInteger(frequencyIndex) || frequencyIndex < 0) {
    throw new Error('Field-plane frequency index must be a non-negative integer');
  }
  if (
    normalizedResponseId.length > 256
    || (normalizedResponseId !== 'system' && !/^(?:channel|member):.+/.test(normalizedResponseId))
  ) throw new Error("Field-plane response id must be 'system', 'channel:<id>', or 'member:<id>'");
  return {
    version: FIELD_PLANE_VERSION,
    request_id: normalizedRequestId,
    plane: validateRequestPlane(plane),
    frequency_index: frequencyIndex,
    response: { id: normalizedResponseId },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function requiredString(header: Record<string, unknown>, key: string): string {
  const value = header[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Field-plane header ${key} must be a non-empty string`);
  }
  return value;
}

function optionalString(header: Record<string, unknown>, key: string): string | null {
  if (header[key] === undefined) return null;
  return requiredString(header, key);
}

function symmetryPlane(header: Record<string, unknown>): string | null {
  const value = header.symmetry_plane;
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string' || !['yz', 'xz', 'xy', 'yz+xz'].includes(value)) {
    throw new Error('Field-plane header symmetry_plane must be null or a supported symmetry plane');
  }
  return value;
}

function requiredFiniteNumber(header: Record<string, unknown>, key: string): number {
  const value = header[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Field-plane header ${key} must be a finite number`);
  }
  return value;
}

function requiredInteger(header: Record<string, unknown>, key: string, minimum = 0): number {
  const value = header[key];
  if (!Number.isSafeInteger(value) || Number(value) < minimum) {
    throw new Error(`Field-plane header ${key} must be an integer of at least ${minimum}`);
  }
  return Number(value);
}

function validateHeader(value: unknown): FieldPlaneHeader {
  if (!isRecord(value)) throw new Error('Field-plane header must be a JSON object');
  if (value.version !== FIELD_PLANE_VERSION && value.version !== FIELD_PLANE_HEADER_VERSION) {
    throw new Error(`Unsupported field-plane version: ${String(value.version)}`);
  }
  if (value.ordering !== FIELD_PLANE_ORDERING) {
    throw new Error(`Unsupported field-plane ordering: ${String(value.ordering)}`);
  }
  const nx = requiredInteger(value, 'nx', 2);
  const ny = requiredInteger(value, 'ny', 2);
  if (nx > 256 || ny > 256 || nx * ny > 65_536) {
    throw new Error(`Field-plane grid dimensions are invalid: ${nx}x${ny}`);
  }
  return {
    version: value.version,
    request_id: requiredString(value, 'request_id'),
    job_id: requiredString(value, 'job_id'),
    frequency_index: requiredInteger(value, 'frequency_index'),
    frequency_hz: requiredFiniteNumber(value, 'frequency_hz'),
    nx,
    ny,
    ordering: FIELD_PLANE_ORDERING,
    phase_convention: requiredString(value, 'phase_convention'),
    pressure_unit: requiredString(value, 'pressure_unit'),
    response_id: requiredString(value, 'response_id'),
    geometry_sha256: requiredString(value, 'geometry_sha256'),
    synthesis_revision: optionalString(value, 'synthesis_revision'),
    symmetry_plane: symmetryPlane(value),
  };
}

/** Decode the versioned little-endian field-plane envelope without relying on
 * payload alignment: a JSON header can leave the float data at any byte offset. */
export function decodeFieldPlane(buffer: ArrayBuffer): DecodedFieldPlane {
  if (buffer.byteLength < 4) throw new Error('Field-plane response is truncated before the header length');
  const view = new DataView(buffer);
  const headerLength = view.getUint32(0, true);
  if (headerLength === 0) throw new Error('Field-plane header length must be greater than zero');
  const payloadOffset = 4 + headerLength;
  if (payloadOffset > buffer.byteLength) throw new Error('Field-plane response is truncated inside the JSON header');

  let parsed: unknown;
  try {
    const headerText = new TextDecoder('utf-8', { fatal: true })
      .decode(new Uint8Array(buffer, 4, headerLength));
    parsed = JSON.parse(headerText);
  } catch (reason) {
    throw new Error('Field-plane JSON header is invalid', { cause: reason });
  }
  const header = validateHeader(parsed);
  const expectedPayloadBytes = header.nx * header.ny * 2 * Float32Array.BYTES_PER_ELEMENT;
  const payloadBytes = buffer.byteLength - payloadOffset;
  if (payloadBytes !== expectedPayloadBytes) {
    throw new Error(`Field-plane payload byte count mismatch: expected ${expectedPayloadBytes}, received ${payloadBytes}`);
  }

  const pointCount = header.nx * header.ny;
  const real = new Float32Array(pointCount);
  const imag = new Float32Array(pointCount);
  for (let index = 0; index < pointCount; index += 1) {
    const offset = payloadOffset + index * 8;
    real[index] = view.getFloat32(offset, true);
    imag[index] = view.getFloat32(offset + 4, true);
  }
  return { header, real, imag };
}

interface FieldPlaneErrorBody {
  code?: string;
  message?: string;
  remedy?: string;
  replacement_request_id?: string;
}

export class FieldPlaneHttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
    readonly replacementRequestId: string | null = null,
    readonly remedy: string | null = null,
  ) {
    super(message);
    this.name = 'FieldPlaneHttpError';
  }
}

async function fieldPlaneHttpError(response: Response): Promise<FieldPlaneHttpError> {
  let message = `${response.status} ${response.statusText}`.trim();
  let code: string | null = null;
  let replacementRequestId: string | null = null;
  let remedy: string | null = null;
  try {
    const body = await response.json() as FieldPlaneErrorBody & {
      detail?: string | FieldPlaneErrorBody;
      error_contract_version?: number;
    };
    if (typeof body.detail === 'string') message = body.detail;
    const structured = isRecord(body.detail) ? body.detail : body;
    if (typeof structured.message === 'string') message = structured.message;
    if (typeof structured.code === 'string') code = structured.code;
    if (typeof structured.remedy === 'string') remedy = structured.remedy;
    if (typeof structured.replacement_request_id === 'string') {
      replacementRequestId = structured.replacement_request_id;
    }
  } catch {
    // The status remains actionable when an intermediary returned non-JSON.
  }
  return new FieldPlaneHttpError(
    response.status,
    message,
    code,
    replacementRequestId,
    remedy,
  );
}

export async function fetchFieldPlane(
  jobId: string,
  request: FieldPlaneRequest,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<DecodedFieldPlane> {
  const response = await fetcher(`/api/results/${encodeURIComponent(jobId)}/field-plane`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/octet-stream' },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) throw await fieldPlaneHttpError(response);
  return decodeFieldPlane(await response.arrayBuffer());
}
