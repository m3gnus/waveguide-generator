/** Small F8-compatible display fixture for decoder and viewport tests. */
import type { FemFieldResponse } from '../../api/femField';

function encode(values: number[], kind: 'f32' | 'u32'): string {
  const bytes = new Uint8Array(values.length * 4);
  const view = new DataView(bytes.buffer);
  values.forEach((value, index) => {
    if (kind === 'f32') view.setFloat32(index * 4, value, true);
    else view.setUint32(index * 4, value, true);
  });
  return btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(''));
}

export function femFieldFixture(frequency = 100, channel = 'front'): FemFieldResponse {
  return {
    schema: 'wg-fem-field-v1', region_id: 'chamber', frequency_hz: frequency,
    points_mm: encode([0, 0, -10, 10, 0, 10, 0, 10, 10, -10, 0, 10], 'f32'),
    tetrahedra: encode([0, 1, 2, 3], 'u32'),
    surfaces: [{ name: 'wg-fem-v1|region=chamber|group=walls|kind=rigid', triangles: encode([0, 1, 2], 'u32') }],
    pressure_real: encode([1, 2, 3, 4], 'f32'), pressure_imag: encode([-1, -2, -3, -4], 'f32'),
    frequencies_hz: [100, 1000], channel_ids: ['front', 'rear'], channel_id: channel,
    phasor_convention: 'exp(-i omega t)', drive_normalization: 'unit_acceleration_m_per_s2',
  };
}
