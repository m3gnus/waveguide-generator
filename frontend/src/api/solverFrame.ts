import { jsonRequest } from './cadlink';
import type { RowMajorMatrix, SolverFrameAxis } from '../viewport/solverFrame';

/** One axis an unlinked model can be solved along (`GET /api/cadlink/solver-frame`). */
export interface SolverFrameAxisOption {
  axis: SolverFrameAxis;
  allowed: boolean;
  /** Why the axis is not offered, in words; null when it is. */
  reason: string | null;
  /** The model's up for this axis under the record's contract (v2), else absent. */
  up?: string | null;
  upSource?: string | null;
  /** The matrix preparation applies to the model for this axis. */
  solverFromAssembly: RowMajorMatrix;
  /** The matrix that turns the previewed record's geometry into this axis's frame. */
  previewFromRecord: RowMajorMatrix;
}

/** The automatic answer to "which way does it radiate" (`server/cadlink/frame_infer.py`).
 * Only `reason` is for people; `reasonCode` is bookkeeping and never shown. */
export interface SolverFrameSuggestion {
  status: 'automatic' | 'ask' | 'unavailable';
  axis: SolverFrameAxis | null;
  confidence?: number | null;
  reason: string;
  reasonCode?: string | null;
  algorithm?: string | null;
  snapshotSha256?: string | null;
}

/** The axis WG shows as chosen, and why: the project's confirmation, a
 * confirmation made under frame contract v1 and carried forward, or the
 * automatic suggestion. Solve confirms it. */
export interface SolverFramePreselection {
  axis: SolverFrameAxis;
  source: 'confirmed' | 'carried' | 'suggested';
}

/** A new snapshot that looks like it faces another way than the project's
 * frame. It never switches anything by itself. */
export interface SolverFrameDiffers {
  confirmedAxis: SolverFrameAxis;
  suggestedAxis: SolverFrameAxis;
  message: string;
}

export interface SolverFrameState {
  linked: false;
  ingestId: string;
  contract: string;
  requirement: { contract: string; export_frame: string; document_up?: string | null } | null;
  /** The frame the record's geometry was meshed in. */
  recordAxis: SolverFrameAxis;
  recordStatesFrame: boolean;
  confirmed: { axis: SolverFrameAxis; confirmedAt: string; frame?: Record<string, unknown> | null } | null;
  axes: SolverFrameAxisOption[];
  /** Absent from a server before the automatic frame (M1e). */
  suggestion?: SolverFrameSuggestion | null;
  preselected?: SolverFramePreselection | null;
  differs?: SolverFrameDiffers | null;
}

export type SolverFramePreview = { linked: true } | SolverFrameState;

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

/** Confirm, for the model's project, the axis it radiates along. The server
 * records whether that agreed with its suggestion or overrode it. */
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
