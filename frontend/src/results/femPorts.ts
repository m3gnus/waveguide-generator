import type { ResultPayload } from './types';

export interface PortPhasor { real: number | null; imaginary: number | null }
export interface PackedPortPressure {
  encoding: 'base64'; dtype: 'complex128'; byte_order: 'little'; order: 'C'; shape: [number, number]; content_base64: string;
}
export type PortPressure = Record<string, PortPhasor[][] | PackedPortPressure>;
export interface FemPorts {
  frequencies: number[];
  drive_normalization: 'unit_acceleration' | 'voltage_rms';
  reference_voltage_rms_v: number | null;
  phase_convention: string;
  observation_angles_deg: number[];
  interfaces: Array<{
    id: string;
    label: string;
    represented_area_m2: number | null;
    symmetry_multiplier?: number;
    /** Area of this port and all its symmetry images. */
    full_model_area_m2?: number | null;
    average_normal_velocity: PortPhasor[];
    radiated_pressure: PortPressure;
  }>;
  other_exterior_radiated_pressure?: PortPressure;
  total_radiated_pressure: PortPressure;
}
export interface PortTrace { name: string; values: Array<number | null> }
export type PortRadiationDisplay = 'spl' | 'phase' | 'real' | 'imaginary';

const decodedPressure = new WeakMap<PackedPortPressure, DataView | null>();
function decodePressure(matrix: PackedPortPressure): DataView | null {
  if (decodedPressure.has(matrix)) return decodedPressure.get(matrix) ?? null;
  let values: DataView | null = null;
  try {
    if (matrix.encoding !== 'base64' || matrix.dtype !== 'complex128'
      || matrix.byte_order !== 'little' || matrix.order !== 'C'
      || !Array.isArray(matrix.shape) || matrix.shape.length !== 2
      || !matrix.shape.every(n => Number.isSafeInteger(n) && n >= 0)
      || typeof matrix.content_base64 !== 'string') throw new Error('Unsupported port encoding');
    const expected = matrix.shape[0] * matrix.shape[1] * 16;
    if (!Number.isSafeInteger(expected)) throw new Error('Invalid port shape');
    const bytes = Uint8Array.from(atob(matrix.content_base64), c => c.charCodeAt(0));
    if (bytes.byteLength !== expected) throw new Error('Invalid port byte length');
    values = new DataView(bytes.buffer);
  } catch { /* A malformed packed matrix invalidates the complete ports block. */ }
  decodedPressure.set(matrix, values);
  return values;
}

export function pressureAt(matrix: PortPhasor[][] | PackedPortPressure | undefined, frequency: number, angle: number): PortPhasor | undefined {
  if (!Number.isInteger(frequency) || !Number.isInteger(angle) || frequency < 0 || angle < 0) return undefined;
  if (!matrix || Array.isArray(matrix)) return matrix?.[frequency]?.[angle];
  const values = decodePressure(matrix);
  if (!values || frequency >= matrix.shape[0] || angle >= matrix.shape[1]) return undefined;
  const offset = (frequency * matrix.shape[1] + angle) * 16;
  return { real: values.getFloat64(offset, true), imaginary: values.getFloat64(offset + 8, true) };
}

export function femPortsOf(result: ResultPayload): FemPorts | null {
  const data = result.metadata?.fem_ports as FemPorts | undefined;
  if (!(data && Array.isArray(data.frequencies) && Array.isArray(data.interfaces)
    && data.interfaces.length && Array.isArray(data.observation_angles_deg))) return null;
  const sources = [...data.interfaces.map(p => p.radiated_pressure), data.total_radiated_pressure,
    ...(data.other_exterior_radiated_pressure ? [data.other_exterior_radiated_pressure] : [])];
  for (const source of sources) {
    if (!source || typeof source !== 'object' || Array.isArray(source)) return null;
    for (const matrix of Object.values(source)) {
      if (Array.isArray(matrix)) continue; // Existing unpacked results remain readable.
      if (!matrix || typeof matrix !== 'object' || !decodePressure(matrix)
        || matrix.shape[0] !== data.frequencies.length
        || matrix.shape[1] !== data.observation_angles_deg.length) return null;
    }
  }
  return data;
}

function finitePhasor(value: PortPhasor | undefined): value is { real: number; imaginary: number } {
  return typeof value?.real === 'number' && Number.isFinite(value.real)
    && typeof value.imaginary === 'number' && Number.isFinite(value.imaginary);
}

export function portVelocityTraces(data: FemPorts): PortTrace[] {
  return data.interfaces.flatMap((port) => ['real', 'imaginary'].map((part) => ({
    name: `${port.label} · ${part === 'real' ? 'Re' : 'Im'}`,
    values: data.frequencies.map((_, i) => {
      const value = port.average_normal_velocity[i];
      return finitePhasor(value) ? value[part as keyof PortPhasor] : null;
    }),
  })));
}

export function portRadiationTraces(data: FemPorts, plane: string, angleIndex: number, display: PortRadiationDisplay): PortTrace[] {
  const sources = [...data.interfaces.map((p) => ({ name: p.label, pressure: p.radiated_pressure })),
    ...(data.other_exterior_radiated_pressure ? [{ name: 'Other exterior sources', pressure: data.other_exterior_radiated_pressure }] : []),
    { name: 'Total', pressure: data.total_radiated_pressure }];
  return sources.map(({ name, pressure }) => ({ name, values: data.frequencies.map((_, i) => {
    const value = pressureAt(pressure[plane], i, angleIndex);
    if (!finitePhasor(value)) return null;
    const magnitude = Math.hypot(value.real, value.imaginary);
    if (display === 'spl') return magnitude > 0 ? 20 * Math.log10(magnitude / 20e-6) : null;
    if (display === 'phase') return magnitude > 0 ? Math.atan2(value.imaginary, value.real) * 180 / Math.PI : null;
    return value[display];
  }) }));
}
