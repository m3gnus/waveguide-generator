import { deadlineFetch } from './deadlineFetch';
import { decodeFemFieldPayload, type FemFieldPayload } from '../viewport/femField';

/** Additive selection metadata; the domain still uses the F5 wire format. */
export interface FemFieldResponse extends FemFieldPayload {
  frequencies_hz: number[];
  channel_ids: string[];
  channel_id: string;
  phasor_convention: 'exp(-i omega t)';
  drive_normalization: 'unit_acceleration_m_per_s2' | 'unit_acceleration' | 'voltage_rms';
  transducer_reference_voltage_v?: number | null;
  density_kg_per_m3?: number | null;
  plane_scopes?: FemPlaneScope[];
  plane_unavailable_reason?: string | null;
}

export async function fetchFemField(jobId: string, frequency?: number, channel?: string, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (frequency !== undefined) query.set('frequency', String(frequency));
  if (channel !== undefined) query.set('channel', channel);
  const response = await deadlineFetch(fetch, `/api/results/${encodeURIComponent(jobId)}/fem-field?${query}`, { signal });
  if (!response.ok) throw new Error(response.status === 404
    ? 'No retained FEM field is available for this run.' : `FEM field request failed (${response.status}).`);
  const payload: FemFieldResponse = await response.json();
  if (!Array.isArray(payload.frequencies_hz) || !payload.frequencies_hz.length
    || payload.frequencies_hz.some((hz) => !Number.isFinite(hz) || hz <= 0)
    || !Array.isArray(payload.channel_ids) || !payload.channel_ids.length
    || payload.channel_ids.some((id) => typeof id !== 'string' || !id)
    || !payload.frequencies_hz.includes(payload.frequency_hz)
    || !payload.channel_ids.includes(payload.channel_id)
    || (payload.density_kg_per_m3 != null && (!Number.isFinite(payload.density_kg_per_m3) || payload.density_kg_per_m3 <= 0))
    || payload.phasor_convention !== 'exp(-i omega t)'
    || !['unit_acceleration_m_per_s2', 'unit_acceleration', 'voltage_rms'].includes(payload.drive_normalization)
    || (payload.drive_normalization === 'voltage_rms'
      && (!Number.isFinite(payload.transducer_reference_voltage_v) || (payload.transducer_reference_voltage_v ?? 0) <= 0))) throw new Error('Invalid FEM field selection metadata');
  return { ...decodeFemFieldPayload(payload), payload };
}

export type DecodedFemField = Awaited<ReturnType<typeof fetchFemField>>;

export type FemPlaneScope = 'interior' | 'exterior' | 'combined';
export type FemPlaneQuantity = 'pressure' | 'velocity_normal';
export const FEM_PLANE_MASK_REASONS = [
  '', 'Solid or outside the selected domain', 'Too close to a surface for a reliable velocity',
] as const;
export interface FemPlaneField {
  positions: Float32Array;
  real: Float32Array;
  imag: Float32Array;
  unit: 'Pa' | 'm/s';
  /** Original row-major grid, including masked points omitted from triangles. */
  samplePositions: Float32Array;
  maskReasons: Uint8Array;
}

/** Convert a masked grid to triangles; never bridge a solid or invalid sample. */
export function decodeFemPlane(payload: {
  nx: number; ny: number; points_mm: string; pressure_real: string; pressure_imag: string;
  valid_mask: string; region_class: string; crosses_body: string; mask_reason?: string; unit: 'Pa' | 'm/s';
}): FemPlaneField {
  const bytes = (value: string) => Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
  const floats = (value: string) => {
    const raw = bytes(value); const view = new DataView(raw.buffer);
    if (raw.length % 4) throw new Error('Invalid coupled plane array');
    return Float32Array.from({ length: raw.length / 4 }, (_, i) => view.getFloat32(i * 4, true));
  };
  const points = floats(payload.points_mm), real = floats(payload.pressure_real), imag = floats(payload.pressure_imag);
  const regions = bytes(payload.region_class), crosses = bytes(payload.crosses_body);
  const valid = bytes(payload.valid_mask), count = payload.nx * payload.ny;
  const reasons = payload.mask_reason === undefined ? Uint8Array.from(valid, (v) => v ? 0 : 1) : bytes(payload.mask_reason);
  if (!Number.isSafeInteger(payload.nx) || !Number.isSafeInteger(payload.ny) || payload.nx < 2 || payload.ny < 2
    || payload.nx > 256 || payload.ny > 256 || points.length !== count * 3 || real.length !== count
    || imag.length !== count || valid.length !== count || valid.some((v) => v > 1)
    || regions.length !== count || regions.some((v) => v > 2)
    || reasons.length !== count || reasons.some((v, i) => v > 2 || (v === 0) !== (valid[i] === 1))
    || crosses.length !== (payload.nx - 1) * (payload.ny - 1) || crosses.some((v) => v > 1)
    || points.some((v) => !Number.isFinite(v)) || real.some((v) => !Number.isFinite(v)) || imag.some((v) => !Number.isFinite(v)))
    throw new Error('Invalid coupled plane grid');
  const indices: number[] = [];
  for (let j = 0; j < payload.ny - 1; j++) for (let i = 0; i < payload.nx - 1; i++) {
    const a = j * payload.nx + i, b = a + 1, c = a + payload.nx, d = c + 1;
    const corners = [a, b, c, d];
    if (crosses[j * (payload.nx - 1) + i] || !corners.every((v) => valid[v] && regions[v] === regions[a])) continue;
    // FEM polygons already represent interior cuts exactly; the grid is air only.
    if (regions[a] !== 2) continue;
    indices.push(a, b, d, a, d, c);
  }
  return { positions: Float32Array.from(indices.flatMap((i) => [points[i * 3], points[i * 3 + 1], points[i * 3 + 2]])),
    real: Float32Array.from(indices.map((i) => real[i])), imag: Float32Array.from(indices.map((i) => imag[i])), unit: payload.unit,
    samplePositions: points, maskReasons: reasons };
}

export async function fetchFemPlane(jobId: string, plane: import('./fieldPlane').FieldPlaneSpec,
  frequency: number, channel: string, scope: FemPlaneScope, quantity: FemPlaneQuantity, signal: AbortSignal) {
  const response = await fetch(`/api/results/${encodeURIComponent(jobId)}/fem-field/plane`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ plane, frequency, channel, scope, quantity }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === 'string' ? error.detail : `Coupled field plane request failed (${response.status}).`);
  }
  const payload = await response.json();
  if (payload.frequency_hz !== frequency || payload.channel_id !== channel || payload.scope !== scope
    || payload.quantity !== quantity || payload.phasor_convention !== 'exp(-i omega t)'
    || payload.unit !== (quantity === 'pressure' ? 'Pa' : 'm/s')) throw new Error('Invalid coupled plane metadata');
  return decodeFemPlane(payload);
}
