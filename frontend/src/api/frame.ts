import { decodeFrame as decodeSharedFrame } from '../../../shared/js/frame.mjs';

export type FrameArray = Float32Array | Float64Array | Uint32Array | Uint16Array | Uint8Array | Int32Array;

export interface FrameSurface {
  role: string;
  positions: string;
  indices: string;
  normals: string;
  shading: 'smooth' | 'flat';
  normalMethod: 'analytic-parametric' | 'analytic-spline' | 'exact-planar';
  closedPhi?: boolean;
  curvatureMean?: string;
  curvaturePrincipal?: string;
}

export interface FrameHeader {
  v: 1;
  kind: 'preview' | 'curve' | 'result-chunk';
  epoch?: number;
  seq?: number;
  designRevision?: number;
  lod?: 'coarse' | 'fine';
  evalMs?: number;
  fidelity?: {
    maxChordErrorMmRequested: number;
    maxNormalStepDegRequested: number;
    minSilhouetteSegmentsRequested: number;
    maxChordErrorMmAchieved: number;
    maxNormalStepDegAchieved: number;
    minSilhouetteSegmentsAchieved: number;
    vertexCapLimited?: boolean;
    surfaces?: Record<string, unknown>;
  };
  surfaces?: FrameSurface[];
  sections: Array<{
    name: string;
    dtype: string;
    shape: number[];
    byteOffset: number;
    byteLength: number;
  }>;
}

export interface DecodedFrame {
  header: FrameHeader;
  sections: Record<string, FrameArray>;
}

// The shared ESM decoder is intentionally the sole untyped import boundary.
const decodeBoundary: any = decodeSharedFrame;

export function decodeFrame(buffer: ArrayBuffer, maxFrameBytes?: number): DecodedFrame {
  return decodeBoundary(buffer, maxFrameBytes === undefined ? undefined : { maxFrameBytes }) as DecodedFrame;
}
