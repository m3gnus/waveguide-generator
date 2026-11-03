import { expect, it, vi } from 'vitest';
import { maxFieldSplDb, splDb } from './fieldPlaneColor';

// The per-sample definition is the reference; the maximum must retain its
// exact value, including the fallback when every sample is unusable.
function referenceMaximum(real: Float32Array, imag: Float32Array): number {
  let maximum = -Infinity;
  for (let index = 0; index < real.length; index += 1) {
    const value = splDb(real[index], imag[index]);
    if (Number.isFinite(value) && value > maximum) maximum = value;
  }
  return Number.isFinite(maximum) ? maximum : 0;
}

function pressureField(count: number): { real: Float32Array; imag: Float32Array } {
  return {
    real: Float32Array.from({ length: count }, (_, index) => Math.sin(index * 0.13) * 10 ** (index % 79 - 40)),
    imag: Float32Array.from({ length: count }, (_, index) => Math.cos(index * 0.17) * 10 ** (index % 79 - 40)),
  };
}

it('preserves the exact SPL maximum across the float32 range and nonfinite samples', () => {
  const field = pressureField(10_000);
  field.real.set([0, -0, NaN, Infinity, -Infinity, 1e-45, -1e-45, 3.4028234663852886e38]);
  field.imag.set([-0, 0, Infinity, NaN, 1, 1e-45, -1e-45, -3.4028234663852886e38]);
  // Include fields with a negative maximum, zero fields, invalid fields and
  // individual edge cases, so a large positive sample cannot mask a change.
  const cases = [field, pressureField(0), { real: new Float32Array(16), imag: new Float32Array(16) },
    { real: Float32Array.of(NaN, Infinity, -Infinity), imag: Float32Array.of(1, NaN, 1) },
    { real: Float32Array.of(1e-45, -1e-45, 1e-20), imag: Float32Array.of(-1e-45, 0, 1e-20) }];
  for (let index = 0; index < 100; index += 1) {
    cases.push({ real: field.real.subarray(index, index + 1), imag: field.imag.subarray(index, index + 1) });
  }
  for (const { real, imag } of cases) expect(maxFieldSplDb(real, imag)).toBe(referenceMaximum(real, imag));
  expect(() => maxFieldSplDb(Float32Array.of(1), new Float32Array())).toThrow('Complex field components must have equal lengths');
});

it('normalizes a large field with one logarithm and identical output', () => {
  const { real, imag } = pressureField(100_000);
  const expected = referenceMaximum(real, imag);
  const logarithm = vi.spyOn(Math, 'log10');
  try {
    const started = performance.now();
    const actual = maxFieldSplDb(real, imag);
    const ms = performance.now() - started;
    const calls = logarithm.mock.calls.length;
    console.info(JSON.stringify({ benchmark: 'field maximum', samples: real.length, logarithms: calls, ms, identicalValue: Object.is(actual, expected) }));
    expect(actual).toBe(expected);
    expect(calls).toBe(1);
  } finally {
    logarithm.mockRestore();
  }
});

it('measures field normalization without instrumenting the timed calls', () => {
  const { real, imag } = pressureField(100_000);
  const expected = referenceMaximum(real, imag);
  const measure = (maximum: typeof maxFieldSplDb) => {
    const samples: number[] = [];
    let actual = 0;
    maximum(real, imag);
    for (let sample = 0; sample < 5; sample += 1) {
      const started = performance.now();
      for (let iteration = 0; iteration < 5; iteration += 1) actual = maximum(real, imag);
      samples.push((performance.now() - started) / 5);
    }
    expect(actual).toBe(expected);
    return samples.sort((left, right) => left - right)[2];
  };
  const beforeMs = measure(referenceMaximum);
  const afterMs = measure(maxFieldSplDb);
  console.info(JSON.stringify({ benchmark: 'field maximum uninstrumented', samples: real.length,
    beforeMs, afterMs, identicalValue: true }));
});
