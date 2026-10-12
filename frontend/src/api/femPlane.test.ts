import { describe, expect, it } from 'vitest';
import { decodeFemPlane } from './femField';

const encode = (values: Float32Array | Uint8Array) => btoa(Array.from(new Uint8Array(values.buffer), (v) => String.fromCharCode(v)).join(''));
const payload = (mask = [1, 1, 1, 1]) => ({ nx: 2, ny: 2, unit: 'Pa' as const,
  points_mm: encode(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0])),
  pressure_real: encode(new Float32Array([1, 2, 3, 4])), pressure_imag: encode(new Float32Array([4, 3, 2, 1])),
  valid_mask: encode(new Uint8Array(mask)) });

describe('masked coupled plane', () => {
  it('retains row-major values and removes triangles touching a solid', () => {
    const all = decodeFemPlane(payload());
    expect(Array.from(all.real)).toEqual([1, 2, 4, 1, 4, 3]);
    const masked = decodeFemPlane(payload([1, 0, 1, 1]));
    expect(Array.from(masked.real)).toEqual([1, 4, 3]);
    expect(masked.positions.length).toBe(9);
  });
  it('rejects bad grid sizes, masks and nonfinite arrays', () => {
    expect(() => decodeFemPlane({ ...payload(), nx: 257 })).toThrow();
    expect(() => decodeFemPlane(payload([1, 2, 1, 1]))).toThrow();
    expect(() => decodeFemPlane({ ...payload(), pressure_real: encode(new Float32Array([NaN, 1, 1, 1])) })).toThrow();
  });
});
