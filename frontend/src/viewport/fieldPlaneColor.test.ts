import { describe, expect, it } from 'vitest';
import {
  buildLutRgba,
  FIELD_PLANE_LUT_SIZE,
  lutIndex,
  maxFieldSplDb,
  REFERENCE_PRESSURE_PA,
  sampleLut,
  splDb,
  windowNormalize,
} from './fieldPlaneColor';
import { FIELD_PLANE_FRAGMENT_SHADER, FIELD_PLANE_VERTEX_SHADER } from './fieldPlaneShader';

describe('field-plane colour oracle', () => {
  it('converts known complex pressures to absolute SPL', () => {
    expect(splDb(REFERENCE_PRESSURE_PA, 0)).toBeCloseTo(0, 10);
    expect(splDb(0, REFERENCE_PRESSURE_PA * 10)).toBeCloseTo(20, 10);
    expect(splDb(REFERENCE_PRESSURE_PA, REFERENCE_PRESSURE_PA)).toBeCloseTo(3.0102999566, 9);
    expect(splDb(0, 0)).toBe(-Infinity);
  });

  it('round-trips a known pressure through the 60 dB window to its LUT texel', () => {
    const pressure = REFERENCE_PRESSURE_PA * 10 ** (-30 / 20);
    const db = splDb(pressure, 0);
    const normalized = windowNormalize(db, -60, 0);
    const lut = buildLutRgba(['#000000', '#ffffff']);
    const sampled = sampleLut(lut, normalized);

    expect(db).toBeCloseTo(-30, 10);
    expect(normalized).toBeCloseTo(0.5, 10);
    expect(sampled.index).toBe(128);
    expect(sampled.rgba).toEqual([128, 128, 128, 255]);
  });

  it('clamps window and LUT indexing at both ends', () => {
    expect(windowNormalize(-90, -60, 0)).toBe(0);
    expect(windowNormalize(20, -60, 0)).toBe(1);
    expect(lutIndex(-1)).toBe(0);
    expect(lutIndex(2)).toBe(FIELD_PLANE_LUT_SIZE - 1);
  });

  it('finds the maximum finite SPL in a decoded complex field', () => {
    expect(maxFieldSplDb(
      Float32Array.of(REFERENCE_PRESSURE_PA, REFERENCE_PRESSURE_PA * 10),
      Float32Array.of(0, 0),
    )).toBeCloseTo(20, 5);
  });

  it('keeps the shader on the same pressure reference and window uniforms', () => {
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('2e-5');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('uWindowMinDb');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('uWindowMaxDb');
  });

  it('includes the viewport clipping chunks in both shader stages', () => {
    expect(FIELD_PLANE_VERTEX_SHADER).toContain('#include <clipping_planes_pars_vertex>');
    expect(FIELD_PLANE_VERTEX_SHADER).toContain('#include <clipping_planes_vertex>');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('#include <clipping_planes_pars_fragment>');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('#include <clipping_planes_fragment>');
  });
});
