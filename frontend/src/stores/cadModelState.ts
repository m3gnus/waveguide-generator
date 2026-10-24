import { useStore } from 'zustand';
import { useCadReturnStore, type CadReturnState } from './cadReturn';
import { cadWorkspace, type CadWorkspaceSnapshot } from './cadWorkspace';

export type CadModelPhase = 'none' | 'selected-unprepared' | 'preparing' | 'prepare-failed' | 'prepared' | 'stale';

/** One projection of the selected CAD model. Preparation and selection keep
 * their existing owners; consumers must not infer model presence from a mesh,
 * document identity, or the parametric design's link classification. */
export function selectCadModelState(
  state: Pick<CadReturnState, 'selectedBundle' | 'ingestRecord' | 'needsIngest' | 'projectLineageId'> & Partial<Pick<CadReturnState, 'captureIngestIntent'>>,
  preparation: Pick<CadWorkspaceSnapshot, 'ingesting' | 'ingestError' | 'preparationIntent'>,
) {
  const bundle = state.selectedBundle;
  const record = state.ingestRecord;
  const selected = !!(bundle || record);
  const currentPreparation = preparation.preparationIntent === undefined
    || preparation.preparationIntent === state.captureIngestIntent?.();
  const phase: CadModelPhase = !selected ? 'none'
    : currentPreparation && preparation.ingesting ? 'preparing'
    : currentPreparation && preparation.ingestError ? 'prepare-failed'
    : record ? state.needsIngest ? 'stale' : 'prepared'
    : 'selected-unprepared';
  const application = bundle?.bundleOrigin === 'onshape' ? 'Onshape'
    : bundle?.bundleOrigin === 'native' ? 'WG' : 'Fusion';
  return {
    phase, selected, bundle, record, application,
    label: bundle?.documentName?.trim() || record?.project?.document_name?.trim() || bundle?.name || 'CAD model',
    lineageId: record?.project?.lineage_id ?? state.projectLineageId ?? bundle?.projectLineageId ?? null,
    authoredInCad: selected && application !== 'WG' && (record?.freshness?.verdict === 'unlinked'
      || (!!bundle && (bundle.designIds ?? []).length === 0)),
    triangles: phase === 'prepared' ? record?.mesh?.stats.triangle_count ?? null : null,
    canPrepare: !!bundle?.readable && bundle.bundleOrigin !== 'native' && phase !== 'preparing',
    needsPreparation: phase === 'selected-unprepared' || phase === 'prepare-failed' || phase === 'stale',
  };
}

export function cadModelState() {
  return selectCadModelState(useCadReturnStore.getState(), cadWorkspace.getSnapshot());
}

export function useCadModelState() {
  const state = useCadReturnStore();
  const preparation = useStore(cadWorkspace);
  return selectCadModelState(state, preparation);
}

export function cadModelStatusLabel(model: ReturnType<typeof selectCadModelState>): string {
  const detail = model.phase === 'prepared' ? model.triangles === null ? 'prepared' : `${model.triangles.toLocaleString()} triangles`
    : model.phase === 'preparing' ? 'preparing…'
    : model.phase === 'prepare-failed' ? 'preparation failed'
    : model.phase === 'stale' ? 'needs preparation'
    : model.phase === 'none' ? 'none selected' : 'not prepared';
  return `CAD model · ${detail}`;
}
