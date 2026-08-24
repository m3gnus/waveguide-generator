import { describe, expect, it } from 'vitest';
import type { AppliedFieldPlaneMask } from './fieldPlaneMaskStore';
import { fieldPlaneMaskedAt, sampleFieldPlaneBilinear, useFieldPlaneProbeStore } from './fieldPlaneProbe';

/** 3x2 grid, v-major with u fastest — the layout the solver flattens to and
 * the shader's texture rows follow. */
function grid() {
  return {
    header: { nx: 3, ny: 2 },
    real: new Float32Array([0, 1, 2, 10, 11, 12]),
    imag: new Float32Array([0, -1, -2, -10, -11, -12]),
  };
}

describe('field-plane probe sampling', () => {
  it('reads the grid corners exactly', () => {
    expect(sampleFieldPlaneBilinear(grid(), 0, 0)).toEqual({ real: 0, imag: 0 });
    expect(sampleFieldPlaneBilinear(grid(), 1, 0)).toEqual({ real: 2, imag: -2 });
    expect(sampleFieldPlaneBilinear(grid(), 0, 1)).toEqual({ real: 10, imag: -10 });
    expect(sampleFieldPlaneBilinear(grid(), 1, 1)).toEqual({ real: 12, imag: -12 });
  });

  it('interpolates between texels', () => {
    const middle = sampleFieldPlaneBilinear(grid(), 0.5, 0.5)!;
    expect(middle.real).toBeCloseTo(6);
    expect(middle.imag).toBeCloseTo(-6);
    const alongU = sampleFieldPlaneBilinear(grid(), 0.25, 0)!;
    expect(alongU.real).toBeCloseTo(0.5);
  });

  it('clamps out-of-range coordinates and refuses undersized buffers', () => {
    expect(sampleFieldPlaneBilinear(grid(), -3, 7)).toEqual({ real: 10, imag: -10 });
    expect(sampleFieldPlaneBilinear(grid(), Number.NaN, 0)).toBeNull();
    expect(sampleFieldPlaneBilinear({
      header: { nx: 3, ny: 2 },
      real: new Float32Array(4),
      imag: new Float32Array(4),
    }, 0, 0)).toBeNull();
  });
});

describe('field-plane interior mask lookup', () => {
  const mask: AppliedFieldPlaneMask = {
    jobId: 'job',
    geometrySha256: 'sha',
    symmetryPlane: null,
    generation: 1,
    nx: 2,
    ny: 2,
    data: new Uint8Array([0, 0, 127, 255]),
  };

  it('reports the interior from half coverage upward at the nearest texel', () => {
    expect(fieldPlaneMaskedAt(mask, 0, 0)).toBe(false);
    expect(fieldPlaneMaskedAt(mask, 0, 1)).toBe(false);
    expect(fieldPlaneMaskedAt(mask, 1, 1)).toBe(true);
    expect(fieldPlaneMaskedAt({ ...mask, data: new Uint8Array([0, 0, 0, 128]) }, 1, 1)).toBe(true);
    expect(fieldPlaneMaskedAt(null, 1, 1)).toBe(false);
  });
});

describe('field-plane probe store', () => {
  it('keeps the same empty state object when hidden twice', () => {
    const reading = {
      localX: 4,
      localY: 5,
      hostWidth: 800,
      hostHeight: 600,
      offsetU_m: 0.1,
      offsetV_m: -0.2,
      point_m: [0, 0, 0] as const,
      real: 1,
      imag: 0,
      masked: false,
    };
    useFieldPlaneProbeStore.getState().show(reading);
    expect(useFieldPlaneProbeStore.getState().reading).toBe(reading);
    useFieldPlaneProbeStore.getState().hide();
    const cleared = useFieldPlaneProbeStore.getState();
    useFieldPlaneProbeStore.getState().hide();
    expect(useFieldPlaneProbeStore.getState()).toBe(cleared);
  });
});
