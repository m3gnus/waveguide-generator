import type { FieldPlaneSpec } from '../api/fieldPlane';

export interface FieldPlaneMaskRequest {
  type: 'classify';
  generation: number;
  jobId: string;
  geometrySha256: string;
  symmetryPlane: string | null;
  plane: FieldPlaneSpec;
}

export interface FieldPlaneMaskResult {
  type: 'result';
  generation: number;
  jobId: string;
  geometrySha256: string;
  symmetryPlane: string | null;
  /** Mask grid dimensions: supersampled relative to the requested plane grid. */
  nx: number;
  ny: number;
  watertight: boolean;
  snappedVertexCount: number;
  /** Coverage per texel, 0 (field visible) to 255 (fully masked). */
  mask: ArrayBuffer;
}

export interface FieldPlaneMaskFailure {
  type: 'error';
  generation: number;
  jobId: string;
  geometrySha256: string;
  symmetryPlane: string | null;
  message: string;
}

export type FieldPlaneMaskResponse = FieldPlaneMaskResult | FieldPlaneMaskFailure;
