import { useStore } from 'zustand';
import { useCurrentModelStatus, type currentModelStatus } from '../model/currentModel';
import { useCadReturnStore, type CadReturnState } from './cadReturn';
import { cadWorkspace, type CadWorkspaceSnapshot } from './cadWorkspace';

export type CadPreparationPhase = 'none' | 'selected-unprepared' | 'preparing' | 'prepare-failed' | 'prepared' | 'stale';
/** `waiting`: a request for this model is held until the user chooses. */
export type CadModelPhase = CadPreparationPhase | 'waiting';

/** The selected model's request status, as `currentModelStatus` classifies it. */
export type CadModelRequest = Pick<ReturnType<typeof currentModelStatus>, 'kind' | 'job'>;

/** One projection of the selected CAD model. Preparation and selection keep
 * their existing owners, and `currentModelStatus` alone matches requests to
 * the model; consumers must not infer model presence from a mesh, document
 * identity, or the parametric design's link classification. */
export function selectCadModelState(
  state: Pick<CadReturnState, 'selectedBundle' | 'ingestRecord' | 'needsIngest' | 'projectLineageId'> & Partial<Pick<CadReturnState, 'captureIngestIntent'>>,
  preparation: Pick<CadWorkspaceSnapshot, 'ingesting' | 'ingestError' | 'preparationIntent'>,
  request: CadModelRequest | null = null,
) {
  const bundle = state.selectedBundle;
  const record = state.ingestRecord;
  const selected = !!(bundle || record);
  const currentPreparation = preparation.preparationIntent === undefined
    || preparation.preparationIntent === state.captureIngestIntent?.();
  const preparationPhase: CadPreparationPhase = !selected ? 'none'
    : currentPreparation && preparation.ingesting ? 'preparing'
    : currentPreparation && preparation.ingestError ? 'prepare-failed'
    : record ? state.needsIngest ? 'stale' : 'prepared'
    : 'selected-unprepared';
  // A held request is the model's state until the user answers its pop-up.
  // An engine hold is the solve readiness gate, which Results describes.
  const heldRequest = selected && preparationPhase !== 'preparing' && request?.kind === 'waiting' && request.job
    && request.job.cad_state?.reason !== 'engine_unavailable' ? request.job : null;
  const phase: CadModelPhase = heldRequest ? 'waiting' : preparationPhase;
  const application = bundle?.bundleOrigin === 'onshape' ? 'Onshape'
    : bundle?.bundleOrigin === 'native' ? 'WG' : 'Fusion';
  return {
    phase, preparation: preparationPhase, heldRequest, selected, bundle, record, application,
    label: bundle?.documentName?.trim() || record?.project?.document_name?.trim() || bundle?.name || 'CAD model',
    lineageId: record?.project?.lineage_id ?? state.projectLineageId ?? bundle?.projectLineageId ?? null,
    authoredInCad: selected && application !== 'WG' && (record?.freshness?.verdict === 'unlinked'
      || (!!bundle && (bundle.designIds ?? []).length === 0)),
    triangles: preparationPhase === 'prepared' ? record?.mesh?.stats.triangle_count ?? null : null,
    canPrepare: !!bundle?.readable && bundle.bundleOrigin !== 'native' && phase !== 'preparing',
    needsPreparation: phase === 'selected-unprepared' || phase === 'prepare-failed' || phase === 'stale',
  };
}

/** Selection and preparation only, for the current-model identity that
 * requests are matched against; presentation uses `useCadModelState`. */
export function cadModelSelection() {
  return selectCadModelState(useCadReturnStore.getState(), cadWorkspace.getSnapshot());
}

export function useCadModelSelection(request: CadModelRequest | null = null) {
  const state = useCadReturnStore();
  const preparation = useStore(cadWorkspace);
  return selectCadModelState(state, preparation, request);
}

/** The selected CAD model, including a request held for the user. */
export function useCadModelState() {
  return useCadModelSelection(useCurrentModelStatus().status);
}

export function cadModelStatusLabel(model: ReturnType<typeof selectCadModelState>): string {
  const detail = model.phase === 'prepared' ? model.triangles === null ? 'prepared' : `${model.triangles.toLocaleString()} triangles`
    : model.phase === 'preparing' ? 'preparing…'
    : model.phase === 'waiting' ? 'waiting for you'
    : model.phase === 'prepare-failed' ? 'preparation failed'
    : model.phase === 'stale' ? 'needs preparation'
    : model.phase === 'none' ? 'none selected' : 'not prepared';
  return `CAD model · ${detail}`;
}
