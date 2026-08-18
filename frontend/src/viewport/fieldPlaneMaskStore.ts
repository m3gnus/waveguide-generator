import { create } from 'zustand';
import type { FieldPlaneMaskFailure, FieldPlaneMaskRequest, FieldPlaneMaskResult } from './fieldPlaneMaskProtocol';

export interface AppliedFieldPlaneMask {
  jobId: string;
  geometrySha256: string;
  generation: number;
  nx: number;
  ny: number;
  data: Uint8Array;
}

interface FieldPlaneMaskState {
  jobId: string | null;
  geometrySha256: string | null;
  generation: number;
  watertight: boolean | null;
  mask: AppliedFieldPlaneMask | null;
  error: string | null;
  begin: (request: FieldPlaneMaskRequest) => void;
  apply: (result: FieldPlaneMaskResult) => void;
  fail: (failure: FieldPlaneMaskFailure) => void;
  clear: () => void;
}

function matchesCurrent(
  state: Pick<FieldPlaneMaskState, 'jobId' | 'geometrySha256' | 'generation'>,
  value: Pick<FieldPlaneMaskRequest, 'jobId' | 'geometrySha256' | 'generation'>,
): boolean {
  return state.jobId === value.jobId
    && state.geometrySha256 === value.geometrySha256
    && state.generation === value.generation;
}

const emptyState = {
  jobId: null,
  geometrySha256: null,
  generation: 0,
  watertight: null,
  mask: null,
  error: null,
} as const;

export const useFieldPlaneMaskStore = create<FieldPlaneMaskState>((set) => ({
  ...emptyState,
  begin: (request) => set((state) => {
    const sameGeometry = state.jobId === request.jobId && state.geometrySha256 === request.geometrySha256;
    return {
      jobId: request.jobId,
      geometrySha256: request.geometrySha256,
      generation: request.generation,
      watertight: sameGeometry ? state.watertight : null,
      mask: sameGeometry ? state.mask : null,
      error: null,
    };
  }),
  apply: (result) => set((state) => matchesCurrent(state, result) ? {
    watertight: result.watertight,
    mask: {
      jobId: result.jobId,
      geometrySha256: result.geometrySha256,
      generation: result.generation,
      nx: result.nx,
      ny: result.ny,
      data: new Uint8Array(result.mask),
    },
    error: null,
  } : state),
  fail: (failure) => set((state) => matchesCurrent(state, failure)
    ? { error: failure.message }
    : state),
  clear: () => set(emptyState),
}));

export function maskMatchesGeometry(
  state: Pick<FieldPlaneMaskState, 'jobId' | 'geometrySha256'>,
  jobId: string | null,
  geometrySha256: string | null,
): boolean {
  return jobId !== null
    && geometrySha256 !== null
    && state.jobId === jobId
    && state.geometrySha256 === geometrySha256;
}
