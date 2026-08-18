import { describe, expect, it } from 'vitest';
import {
  advanceFieldPlanePhase,
  buildLutRgba,
  fieldPlaneDisplayValue,
  fieldPlaneWindowForMode,
  FIELD_PLANE_DIVERGING_COLORMAP,
  FIELD_PLANE_ISOLINE_STEP_DB,
  FIELD_PLANE_LUT_SIZE,
  FIELD_PLANE_NORMALIZED_MIN_DB,
  FIELD_PLANE_PHASE_COLORMAP,
  FIELD_PLANE_PHASE_LIMIT_DEGREES,
  instantaneousPressure,
  isolineCoordinate,
  isolineDistanceDb,
  lutIndex,
  maxFieldMagnitudePa,
  maxFieldSplDb,
  normalizedSplDb,
  phaseDegrees,
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

  it('reports normalized SPL relative to the field maximum in a fixed window', () => {
    const maximum = splDb(REFERENCE_PRESSURE_PA * 100, 0);
    expect(normalizedSplDb(REFERENCE_PRESSURE_PA, 0, maximum)).toBeCloseTo(-40, 10);
    expect(fieldPlaneDisplayValue('normalized', REFERENCE_PRESSURE_PA, 0, {
      fieldMaxSplDb: maximum,
      phaseRadians: 0,
    })).toBeCloseTo(-40, 10);
    expect(fieldPlaneWindowForMode(
      'normalized',
      Float32Array.of(1),
      Float32Array.of(0),
    )).toEqual({ minimum: FIELD_PLANE_NORMALIZED_MIN_DB, maximum: 0, unit: 'dB' });
  });

  it('maps complex argument to signed phase degrees in the cyclic window', () => {
    expect(phaseDegrees(0, 1)).toBeCloseTo(90, 10);
    expect(phaseDegrees(-1, 0)).toBeCloseTo(180, 10);
    expect(phaseDegrees(0, -1)).toBeCloseTo(-90, 10);
    expect(fieldPlaneDisplayValue('phase', 1, 1, { fieldMaxSplDb: 0, phaseRadians: 0 })).toBeCloseTo(45, 10);
    expect(fieldPlaneWindowForMode('phase', Float32Array.of(1), Float32Array.of(0))).toEqual({
      minimum: -FIELD_PLANE_PHASE_LIMIT_DEGREES,
      maximum: FIELD_PLANE_PHASE_LIMIT_DEGREES,
      unit: 'deg',
    });
  });

  it('uses re*cos(wt) + im*sin(wt), so a +z wave moves toward +z', () => {
    const omegaT = Math.PI / 3;
    const waveNumber = 2;
    const positiveZ = omegaT / waveNumber;
    const phasorAt = (z: number): [number, number] => [
      Math.cos(waveNumber * z),
      Math.sin(waveNumber * z),
    ];
    const [realPositive, imagPositive] = phasorAt(positiveZ);
    const [realNegative, imagNegative] = phasorAt(-positiveZ);

    expect(instantaneousPressure(3, 4, 0)).toBe(3);
    expect(instantaneousPressure(realPositive, imagPositive, omegaT)).toBeCloseTo(1, 10);
    expect(instantaneousPressure(realNegative, imagNegative, omegaT)).toBeCloseTo(-0.5, 10);
    expect(fieldPlaneDisplayValue('instantaneous', realPositive, imagPositive, {
      fieldMaxSplDb: 0,
      phaseRadians: omegaT,
    })).toBeCloseTo(1, 10);
  });

  it('uses a phase-independent 98th-percentile complex-magnitude window', () => {
    const real = new Float32Array(100);
    const imag = Float32Array.from({ length: 100 }, (_, index) => (
      index === 99 ? 1_000 : index + 1
    ));
    expect(maxFieldMagnitudePa(real, imag)).toBe(1_000);
    expect(fieldPlaneWindowForMode('instantaneous', real, imag)).toEqual({
      minimum: -98,
      maximum: 98,
      unit: 'Pa',
    });
  });

  it('keeps a nonzero instantaneous window for degenerate fields', () => {
    expect(fieldPlaneWindowForMode(
      'instantaneous',
      new Float32Array(),
      new Float32Array(),
    )).toEqual({ minimum: -1, maximum: 1, unit: 'Pa' });
    expect(fieldPlaneWindowForMode(
      'instantaneous',
      new Float32Array(100),
      new Float32Array(100),
    )).toEqual({ minimum: -1, maximum: 1, unit: 'Pa' });

    const sparse = new Float32Array(100);
    sparse[99] = 7;
    expect(fieldPlaneWindowForMode('instantaneous', sparse, new Float32Array(100))).toEqual({
      minimum: -7,
      maximum: 7,
      unit: 'Pa',
    });
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

  it('builds cyclic and diverging LUTs for phase and instantaneous pressure', () => {
    const cyclic = buildLutRgba(FIELD_PLANE_PHASE_COLORMAP);
    const diverging = buildLutRgba(FIELD_PLANE_DIVERGING_COLORMAP);
    expect([...cyclic.slice(0, 4)]).toEqual([...cyclic.slice(-4)]);
    expect(sampleLut(diverging, 0.5).rgba).toEqual([231, 227, 219, 255]);
  });

  it('places isolines on exact 6 dB multiples', () => {
    expect(isolineCoordinate(-18)).toBe(-3);
    expect(isolineDistanceDb(-18)).toBe(0);
    expect(isolineDistanceDb(-15)).toBeCloseTo(FIELD_PLANE_ISOLINE_STEP_DB / 2, 10);
    expect(isolineDistanceDb(1.5)).toBeCloseTo(1.5, 10);
  });

  it('advances visual phase by cycles per second and wraps full turns', () => {
    expect(advanceFieldPlanePhase(0, 0.25, 1)).toBeCloseTo(Math.PI / 2, 10);
    expect(advanceFieldPlanePhase(Math.PI * 1.5, 0.5, 1)).toBeCloseTo(Math.PI / 2, 10);
  });

  it('keeps shader mode, convention, window, and isoline literals from drifting', () => {
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('2e-5 * 1e-12');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('uniform sampler2D uFieldComplex');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('uWindowMin');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('uWindowMax');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('180.0 / 3.141592653589793');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('field.x * cos(uTimePhase) + field.y * sin(uTimePhase)');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('value / 6.0');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('fwidth(isoline)');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('uniform sampler2D uMask');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('discard');
  });

  it('includes the viewport clipping chunks in both shader stages', () => {
    expect(FIELD_PLANE_VERTEX_SHADER).toContain('#include <clipping_planes_pars_vertex>');
    expect(FIELD_PLANE_VERTEX_SHADER).toContain('#include <clipping_planes_vertex>');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('#include <clipping_planes_pars_fragment>');
    expect(FIELD_PLANE_FRAGMENT_SHADER).toContain('#include <clipping_planes_fragment>');
  });
});
