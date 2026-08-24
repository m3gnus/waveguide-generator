import { create } from 'zustand';
import type { DecodedFieldPlane } from '../api/fieldPlane';
import type { AppliedFieldPlaneMask } from './fieldPlaneMaskStore';

export interface FieldPlaneSample {
  real: number;
  imag: number;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** Bilinear read of the complex pressure grid. `u`/`v` are the normalised
 * plane coordinates the shader samples with, and the grid is stored v-major
 * with u varying fastest — the same `j * nx + i` layout the solver builds. */
export function sampleFieldPlaneBilinear(
  field: Pick<DecodedFieldPlane, 'real' | 'imag'> & { header: Pick<DecodedFieldPlane['header'], 'nx' | 'ny'> },
  u: number,
  v: number,
): FieldPlaneSample | null {
  const { nx, ny } = field.header;
  if (!Number.isFinite(u) || !Number.isFinite(v)) return null;
  if (nx < 1 || ny < 1 || field.real.length < nx * ny || field.imag.length < nx * ny) return null;
  const x = clamp01(u) * (nx - 1);
  const y = clamp01(v) * (ny - 1);
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const x1 = Math.min(nx - 1, x0 + 1);
  const y1 = Math.min(ny - 1, y0 + 1);
  const fx = x - x0;
  const fy = y - y0;
  const weights: ReadonlyArray<[number, number]> = [
    [y0 * nx + x0, (1 - fx) * (1 - fy)],
    [y0 * nx + x1, fx * (1 - fy)],
    [y1 * nx + x0, (1 - fx) * fy],
    [y1 * nx + x1, fx * fy],
  ];
  let real = 0;
  let imag = 0;
  for (const [index, weight] of weights) {
    real += field.real[index] * weight;
    imag += field.imag[index] * weight;
  }
  return { real, imag };
}

/** Nearest-texel read of the coverage mask. The mask carries an anti-aliasing
 * ramp, so the probe reports "inside" from half coverage upward — the point
 * where the shader has faded the field to mostly hidden. */
export function fieldPlaneMaskedAt(mask: AppliedFieldPlaneMask | null, u: number, v: number): boolean {
  if (!mask || mask.nx < 1 || mask.ny < 1) return false;
  const x = Math.round(clamp01(u) * (mask.nx - 1));
  const y = Math.round(clamp01(v) * (mask.ny - 1));
  return (mask.data[y * mask.nx + x] ?? 0) >= 128;
}

export interface FieldPlaneProbeReading {
  /** Pointer position relative to the canvas host, which the stylesheet pins
   * to the viewport panel's own box — so these are also the coordinates the
   * absolutely positioned tooltip uses. */
  localX: number;
  localY: number;
  hostWidth: number;
  hostHeight: number;
  offsetU_m: number;
  offsetV_m: number;
  point_m: readonly [number, number, number];
  real: number;
  imag: number;
  /** True where the shader discards the texel because it lies inside the model. */
  masked: boolean;
}

export interface FieldPlaneProbeState {
  reading: FieldPlaneProbeReading | null;
  show: (reading: FieldPlaneProbeReading) => void;
  hide: () => void;
}

export const useFieldPlaneProbeStore = create<FieldPlaneProbeState>((set) => ({
  reading: null,
  show: (reading) => set({ reading }),
  hide: () => set((state) => (state.reading === null ? state : { reading: null })),
}));
