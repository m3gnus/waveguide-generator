import { afterEach, describe, expect, it, vi } from 'vitest';
import { decodeFemPlane, fetchFemPlane } from './femField';

const encode = (values: Float32Array | Uint8Array) => btoa(Array.from(new Uint8Array(values.buffer), (v) => String.fromCharCode(v)).join(''));
const payload = (mask = [1, 1, 1, 1]) => ({ nx: 2, ny: 2, unit: 'Pa' as const,
  points_mm: encode(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0])),
  pressure_real: encode(new Float32Array([1, 2, 3, 4])), pressure_imag: encode(new Float32Array([4, 3, 2, 1])),
  region_class: encode(new Uint8Array([2, 2, 2, 2])), crosses_body: encode(new Uint8Array([0])),
  valid_mask: encode(new Uint8Array(mask)) });

describe('masked coupled plane', () => {
  it('retains row-major values and removes triangles touching a solid', () => {
    const all = decodeFemPlane(payload());
    expect(Array.from(all.real)).toEqual([1, 2, 4, 1, 4, 3]);
    const masked = decodeFemPlane(payload([1, 0, 1, 1]));
    expect(Array.from(masked.real)).toEqual([]);
    expect(masked.positions.length).toBe(0);
  });
  it('rejects bad grid sizes, masks and nonfinite arrays', () => {
    expect(() => decodeFemPlane({ ...payload(), nx: 257 })).toThrow();
    expect(() => decodeFemPlane(payload([1, 2, 1, 1]))).toThrow();
    expect(() => decodeFemPlane({ ...payload(), pressure_real: encode(new Float32Array([NaN, 1, 1, 1])) })).toThrow();
  });
});

afterEach(() => vi.restoreAllMocks());

it('drops sub-grid solid crossings and every mixed interior/exterior cell', () => {
  expect(decodeFemPlane({ ...payload(), crosses_body: encode(new Uint8Array([1])) }).positions.length).toBe(0);
  expect(decodeFemPlane({ ...payload(), region_class: encode(new Uint8Array([2, 1, 2, 2])) }).positions.length).toBe(0);
  expect(decodeFemPlane({ ...payload(), region_class: encode(new Uint8Array([1, 1, 1, 1])) }).positions.length).toBe(0);
  expect(() => decodeFemPlane({ ...payload(), crosses_body: '' })).toThrow('grid');
});

it('preserves server error detail for unavailable runtimes and corrupt traces', async () => {
  const plane = { origin_m: [0, 0, 0] as [number, number, number], axis_u: [1, 0, 0] as [number, number, number],
    axis_v: [0, 1, 0] as [number, number, number], width_m: 1, height_m: 1, nx: 2, ny: 2 };
  const spy = vi.spyOn(globalThis, 'fetch');
  for (const status of [409, 410]) {
    spy.mockResolvedValue(new Response(JSON.stringify({ detail: 'BEAT runtime unavailable' }), { status }));
    await expect(fetchFemPlane('one', plane, 100, 'front', 'combined', 'pressure', new AbortController().signal))
      .rejects.toThrow('BEAT runtime unavailable');
  }
});
