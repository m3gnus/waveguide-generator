import { decodeFemFieldPayload, type FemFieldPayload } from '../viewport/femField';

/** Additive selection metadata; the domain still uses the F5 wire format. */
export interface FemFieldResponse extends FemFieldPayload {
  frequencies_hz: number[];
  channel_ids: string[];
  channel_id: string;
  phasor_convention: 'exp(-i omega t)';
  drive_normalization: 'unit_acceleration_m_per_s2' | 'unit_acceleration' | 'voltage_rms';
  transducer_reference_voltage_v?: number | null;
}

export async function fetchFemField(jobId: string, frequency?: number, channel?: string, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (frequency !== undefined) query.set('frequency', String(frequency));
  if (channel !== undefined) query.set('channel', channel);
  const response = await fetch(`/api/results/${encodeURIComponent(jobId)}/fem-field?${query}`, { signal });
  if (!response.ok) throw new Error(response.status === 404
    ? 'No retained FEM field is available for this run.' : `FEM field request failed (${response.status}).`);
  const payload: FemFieldResponse = await response.json();
  if (!Array.isArray(payload.frequencies_hz) || !payload.frequencies_hz.length
    || payload.frequencies_hz.some((hz) => !Number.isFinite(hz) || hz <= 0)
    || !Array.isArray(payload.channel_ids) || !payload.channel_ids.length
    || payload.channel_ids.some((id) => typeof id !== 'string' || !id)
    || !payload.frequencies_hz.includes(payload.frequency_hz)
    || !payload.channel_ids.includes(payload.channel_id)
    || payload.phasor_convention !== 'exp(-i omega t)'
    || !['unit_acceleration_m_per_s2', 'unit_acceleration', 'voltage_rms'].includes(payload.drive_normalization)
    || (payload.drive_normalization === 'voltage_rms'
      && (!Number.isFinite(payload.transducer_reference_voltage_v) || (payload.transducer_reference_voltage_v ?? 0) <= 0))) throw new Error('Invalid FEM field selection metadata');
  return { ...decodeFemFieldPayload(payload), payload };
}

export type DecodedFemField = Awaited<ReturnType<typeof fetchFemField>>;
