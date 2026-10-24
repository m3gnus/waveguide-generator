import { createStore } from 'zustand/vanilla';
import type { CadReturnBundle, CadReturnIngestRecord, FusionCadStatus } from '../api/cadlink';
import type { OnshapeConnection, OnshapeStatus } from '../api/onshape';
import type { WgLinkExportResponse } from '../api/designIo';
import type { DesignIdentity } from './document';
import type { RefreshOptions } from '../shell/cadlink/arrivals';

/** A report belonging to one error message: the protocol-level evidence a
 * surface puts behind a disclosure rather than in the sentence it shows.
 *
 * `message` names the error it belongs to, so a surface renders it only while
 * that exact error is the one on screen. */
export interface ErrorDiagnostics {
  message: string;
  detail: string;
}

export interface CadWorkspaceSnapshot {
  bundles: CadReturnBundle[];
  loading: boolean;
  ingesting: boolean;
  ingestError: string | null;
  /** Preparation feedback belongs to this CAD intent, including after failure. */
  preparationIntent?: number;
  sendingToFusion: boolean;
  pullingFromFusion: boolean;
  error: string | null;
  errorDiagnostics: ErrorDiagnostics | null;
  status: string | null;
  viewportNotice: string | null;
  fusionStatus: FusionCadStatus | null;
  onshapeStatus: OnshapeStatus | null;
  onshapeConnection: OnshapeConnection | null;
  pendingFusionConflict: boolean;
  refresh(options?: RefreshOptions): Promise<void>;
  refreshOnshapeStatus(committed?: DesignIdentity): Promise<void>;
  returnFromOnshape(): Promise<void>;
  selectBundle(bundle: CadReturnBundle, projectLineageId?: string | null): void;
  /** `meshLimitOverride`: the user chose Solve anyway over the triangle ceiling. */
  ingest(options?: { meshLimitOverride?: boolean }): Promise<void>;
  ingestSelected(options?: { meshLimitOverride?: boolean }): Promise<CadReturnIngestRecord>;
  pullFromFusion(): Promise<CadReturnBundle>;
  pullAndSolve(): Promise<'solving' | 'blocked' | 'failed'>;
  /** The one Fusion outbound path: derives open-vs-update and the expected
   * document guard from current evidence, and parks on the two-way conflict
   * (returning null) until the user confirms through the shared conflict dialog. */
  sendWgToFusion(options?: { confirmed?: boolean }): Promise<WgLinkExportResponse | null>;
  cancelFusionConflict(): void;
  clearFeedback(): void;
  reportError(message: string): void;
  reportStatus(message: string): void;
  reportViewportNotice(message: string | null): void;
  selectFusionInstance(instanceId: string): void;
  selectOnshapeInstance(instanceId: string): void;
}

const unavailable = async () => { throw new Error('CAD workspace is unavailable'); };
const unavailableRefreshOnshape = async (_committed?: DesignIdentity) => unavailable();
export const emptyCadWorkspace: CadWorkspaceSnapshot = {
  bundles: [],
  loading: true,
  ingesting: false,
  ingestError: null,
  preparationIntent: undefined,
  sendingToFusion: false,
  pullingFromFusion: false,
  error: null,
  errorDiagnostics: null,
  status: null,
  viewportNotice: null,
  fusionStatus: null,
  onshapeStatus: null,
  onshapeConnection: null,
  pendingFusionConflict: false,
  refresh: unavailable,
  refreshOnshapeStatus: unavailableRefreshOnshape,
  returnFromOnshape: unavailable,
  selectBundle: () => undefined,
  ingest: unavailable,
  ingestSelected: unavailable,
  pullFromFusion: unavailable,
  pullAndSolve: unavailable,
  sendWgToFusion: unavailable,
  cancelFusionConflict: () => undefined,
  clearFeedback: () => undefined,
  reportError: () => undefined,
  reportStatus: () => undefined,
  reportViewportNotice: () => undefined,
  selectFusionInstance: () => undefined,
  selectOnshapeInstance: () => undefined,
};

/** UI projection and commands supplied by the mounted independent owners. */
const store = createStore<CadWorkspaceSnapshot>(() => emptyCadWorkspace);
export const cadWorkspace = Object.assign(store, { getSnapshot: store.getState });
