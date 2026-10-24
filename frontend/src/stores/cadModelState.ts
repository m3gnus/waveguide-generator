import { useStore } from 'zustand';
import { useSyncExternalStore } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useCurrentModelStatus, type currentModelStatus } from '../model/currentModel';
import { useCadReturnStore, type CadReturnState } from './cadReturn';
import { useDocumentStore } from './document';
import { cadWorkspace, type CadWorkspaceSnapshot } from './cadWorkspace';
import { meshCeilingChoice, type MeshCeilingChoice } from './meshCeilingChoice';

/** `stopped`: the model has no prepared mesh, and the server's request for
 * it was refused or its solve failed; `failureMessage` says why. */
export type CadPreparationPhase = 'none' | 'selected-unprepared' | 'preparing' | 'prepare-failed' | 'stopped' | 'prepared' | 'stale';
/** `waiting`: a request for this model is held until the user chooses, or a
 * preparation waits for the user's mesh-size choice (`meshChoice`). */
export type CadModelPhase = CadPreparationPhase | 'waiting';

/** The selected model's request status, as `currentModelStatus` classifies it. */
export type CadModelRequest = Pick<ReturnType<typeof currentModelStatus>, 'kind' | 'job'>
  & Partial<Pick<ReturnType<typeof currentModelStatus>, 'message'>>;

/** One projection of the selected CAD model. Preparation and selection keep
 * their existing owners, and `currentModelStatus` alone matches requests to
 * the model; consumers must not infer model presence from a mesh, document
 * identity, or the parametric design's link classification. */
export function selectCadModelState(
  state: Pick<CadReturnState, 'selectedBundle' | 'ingestRecord' | 'needsIngest' | 'projectLineageId'> & Partial<Pick<CadReturnState, 'captureIngestIntent'>>,
  preparation: Pick<CadWorkspaceSnapshot, 'ingesting' | 'ingestError' | 'preparationIntent'>,
  request: CadModelRequest | null = null,
  /** The open WG design: a bundle naming it is WG's own design, linked. */
  openDesignId: string | null = null,
  /** A triangle-ceiling question asked in this window (`meshCeilingChoice`). */
  ceiling: MeshCeilingChoice | null = null,
) {
  const bundle = state.selectedBundle;
  const record = state.ingestRecord;
  const selected = !!(bundle || record);
  const currentPreparation = preparation.preparationIntent === undefined
    || preparation.preparationIntent === state.captureIngestIntent?.();
  // Without a prepared mesh, the server's job for the selected request (a
  // Fusion request WG prepares itself) is this model's preparation.
  const serverPreparing = !record && request?.kind === 'preparing';
  const serverFailed = !record && request?.kind === 'prepare-failed' && !!request.job;
  // Refused, or solved and failed, before WG had a mesh of it on screen.
  const serverStopped = !record && (request?.kind === 'refused' || request?.kind === 'failed') && !!request.job;
  const preparationPhase: CadPreparationPhase = !selected ? 'none'
    : currentPreparation && preparation.ingesting ? 'preparing'
    : currentPreparation && preparation.ingestError ? 'prepare-failed'
    : record ? state.needsIngest ? 'stale' : 'prepared'
    : serverPreparing ? 'preparing'
    : serverFailed ? 'prepare-failed'
    : serverStopped ? 'stopped'
    : 'selected-unprepared';
  const failureMessage = !selected ? null
    : preparationPhase === 'prepare-failed' && serverFailed
      ? [request?.job?.cad_state?.message, request?.job?.cad_state?.next_step].filter(Boolean).join(' ') || null
    : preparationPhase === 'stopped' ? request?.message || null
    : null;
  // A ceiling question for this model: asked, or put off with Later.
  const meshChoice = selected && !!bundle && ceiling?.bundlePath === bundle.bundlePath
    && preparationPhase !== 'preparing' && preparationPhase !== 'prepared' ? ceiling : null;
  // A held request is the model's state until the user answers its pop-up.
  // An engine hold is the solve readiness gate, which Results describes.
  const heldRequest = selected && preparationPhase !== 'preparing' && request?.kind === 'waiting' && request.job
    && request.job.cad_state?.reason !== 'engine_unavailable' ? request.job : null;
  const phase: CadModelPhase = heldRequest || meshChoice ? 'waiting' : preparationPhase;
  // A record without a bundle (a restored run) names no application; it is
  // never assumed to be Fusion.
  const application = bundle?.bundleOrigin === 'onshape' ? 'Onshape'
    : bundle?.bundleOrigin === 'native' ? 'WG' : bundle ? 'Fusion' : 'CAD';
  return {
    phase, preparation: preparationPhase, heldRequest, meshChoice, failureMessage, selected, bundle, record, application,
    label: bundle?.documentName?.trim() || record?.project?.document_name?.trim() || bundle?.name || 'CAD model',
    lineageId: record?.project?.lineage_id ?? state.projectLineageId ?? bundle?.projectLineageId ?? null,
    // A selected CAD model is authored in CAD unless its own evidence says WG
    // made it: a linked freshness verdict, or a bundle naming the open design.
    // A record without freshness or bundle, or a bundle naming another WG
    // design, is still the CAD model: parametric Open/Send never applies to it.
    authoredInCad: selected && application !== 'WG' && !((record?.freshness?.verdict
      ? record.freshness.verdict !== 'unlinked' : !!bundle)
      && (!bundle || (!!openDesignId && (bundle.designIds ?? []).includes(openDesignId)))),
    triangles: preparationPhase === 'prepared' ? record?.mesh?.stats.triangle_count ?? null : null,
    canPrepare: !!bundle?.readable && bundle.bundleOrigin !== 'native' && phase !== 'preparing',
    // A model waiting for its mesh-size choice can still be prepared again
    // (with other sizes), which answers the question.
    needsPreparation: phase === 'selected-unprepared' || phase === 'prepare-failed' || phase === 'stopped' || phase === 'stale'
      || !!meshChoice,
  };
}

/** Selection and preparation only, for the current-model identity that
 * requests are matched against; presentation uses `useCadModelState`. */
export function cadModelSelection() {
  return selectCadModelState(useCadReturnStore.getState(), cadWorkspace.getSnapshot(), null,
    useDocumentStore.getState().identity?.designId ?? null, meshCeilingChoice.getSnapshot());
}

/** Subscribes to the fields the projection reads only, so a listing poll or a
 * status message does not re-render every consumer. */
export function useCadModelSelection(request: CadModelRequest | null = null) {
  const state = useCadReturnStore(useShallow(s => ({
    selectedBundle: s.selectedBundle, ingestRecord: s.ingestRecord, needsIngest: s.needsIngest,
    projectLineageId: s.projectLineageId, captureIngestIntent: s.captureIngestIntent,
    // The ingest intent is a counter inside the store's closure; its
    // generation changes with the state the store publishes for it.
    ingestGeneration: s.captureIngestIntent(),
  })));
  const preparation = useStore(cadWorkspace, useShallow(s => ({
    ingesting: s.ingesting, ingestError: s.ingestError, preparationIntent: s.preparationIntent,
  })));
  const openDesignId = useDocumentStore(s => s.identity?.designId ?? null);
  const ceiling = useSyncExternalStore(meshCeilingChoice.subscribe, meshCeilingChoice.getSnapshot, meshCeilingChoice.getSnapshot);
  return selectCadModelState(state, preparation, request, openDesignId, ceiling);
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
    : model.phase === 'stopped' ? 'not solved'
    : model.phase === 'stale' ? 'needs preparation'
    : model.phase === 'none' ? 'none selected' : 'not prepared';
  return `CAD model · ${detail}`;
}

/** The CAD project header's line for a selected model that has no project yet. */
export function cadModelHeaderLine(model: ReturnType<typeof selectCadModelState>): string {
  if (model.phase === 'preparing') return `Preparing ${model.label}…`;
  if (model.phase === 'waiting') return model.meshChoice
    ? meshChoiceLine(model.label)
    : 'Waiting for you. WG needs your choice before it can continue.';
  if (model.phase === 'prepare-failed' || model.phase === 'stopped') return preparationFailureLine(model);
  return 'Prepare simulation to use this model in WG.';
}

/** The one sentence for a model waiting on the triangle-ceiling choice. */
export function meshChoiceLine(label: string): string {
  return `${label} is waiting for your mesh-size choice.`;
}

/** A failed or stopped model's line: WG's words when it has them, and Prepare
 * simulation only where that button exists (a Fusion request pointer has none). */
export function preparationFailureLine(model: Pick<ReturnType<typeof selectCadModelState>, 'failureMessage' | 'canPrepare'>): string {
  if (model.failureMessage) return model.failureMessage;
  return model.canPrepare ? 'Preparation failed. Use Prepare simulation to try again.' : 'Preparation failed.';
}
