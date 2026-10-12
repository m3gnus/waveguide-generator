import type { ResultPayload } from './types';

export interface PortPhasor { real: number | null; imaginary: number | null }
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
    average_normal_velocity: PortPhasor[];
    radiated_pressure: Record<string, PortPhasor[][]>;
  }>;
  diaphragm_radiated_pressure: Record<string, PortPhasor[][]>;
  total_radiated_pressure: Record<string, PortPhasor[][]>;
}
export interface PortTrace { name: string; values: Array<number | null> }
export type PortRadiationDisplay = 'spl' | 'phase' | 'real' | 'imaginary';

export function femPortsOf(result: ResultPayload): FemPorts | null {
  const data = result.metadata?.fem_ports as FemPorts | undefined;
  return data && Array.isArray(data.frequencies) && Array.isArray(data.interfaces)
    && data.interfaces.length && Array.isArray(data.observation_angles_deg) ? data : null;
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
    { name: 'Diaphragm only', pressure: data.diaphragm_radiated_pressure },
    { name: 'Total', pressure: data.total_radiated_pressure }];
  return sources.map(({ name, pressure }) => ({ name, values: data.frequencies.map((_, i) => {
    const value = pressure[plane]?.[i]?.[angleIndex];
    if (!finitePhasor(value)) return null;
    const magnitude = Math.hypot(value.real, value.imaginary);
    if (display === 'spl') return magnitude > 0 ? 20 * Math.log10(magnitude / 20e-6) : null;
    if (display === 'phase') return magnitude > 0 ? Math.atan2(value.imaginary, value.real) * 180 / Math.PI : null;
    return value[display];
  }) }));
}
