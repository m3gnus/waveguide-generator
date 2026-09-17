import { jsonRequest } from './cadlink';
import type { RowMajorMatrix, SolverFrameAxis } from '../viewport/solverFrame';

/** One axis an unlinked model can be solved along (`GET /api/cadlink/solver-frame`). */
export interface SolverFrameAxisOption {
  axis: SolverFrameAxis;
  allowed: boolean;
  reason: string | null;
  /** The matrix preparation applies to the model for this axis. */
  solverFromAssembly: RowMajorMatrix;
  /** The matrix that turns the previewed record's geometry into this axis's frame. */
  previewFromRecord: RowMajorMatrix;
}

export type SolverFramePreview =
  | { linked: true }
  | {
    linked: false;
    ingestId: string;
    contract: string;
    requirement: { contract: string; export_frame: string } | null;
    /** The frame the record's geometry was meshed in. */
    recordAxis: SolverFrameAxis;
    recordStatesFrame: boolean;
    confirmed: { axis: SolverFrameAxis; confirmedAt: string } | null;
    axes: SolverFrameAxisOption[];
  };

export type SolverFrameSnapshot = { operationId: string } | { ingestId: string };

export function getSolverFrame(
  snapshot: SolverFrameSnapshot,
  fetcher: typeof fetch = fetch,
): Promise<SolverFramePreview> {
  const query = 'operationId' in snapshot
    ? `operationId=${encodeURIComponent(snapshot.operationId)}`
    : `ingestId=${encodeURIComponent(snapshot.ingestId)}`;
  return jsonRequest(`/api/cadlink/solver-frame?${query}`, undefined, fetcher);
}

/** Confirm, for the model's project, the axis it radiates along. */
export function confirmSolverFrame(
  snapshot: SolverFrameSnapshot,
  axis: SolverFrameAxis,
  fetcher: typeof fetch = fetch,
): Promise<SolverFramePreview> {
  return jsonRequest('/api/cadlink/solver-frame', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...snapshot, axis }),
  }, fetcher);
}
