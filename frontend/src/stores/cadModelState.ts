import { useStore } from 'zustand';
import { useSyncExternalStore } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useCurrentModelStatus, type currentModelStatus } from '../model/currentModel';
import { preparesAutomatically, useCadReturnStore, type CadReturnState, type CadPreparationInputs } from './cadReturn';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { useDocumentStore } from './document';
import { cadWorkspace, type CadWorkspaceSnapshot } from './cadWorkspace';
import { meshCeilingChoice, type MeshCeilingChoice } from './meshCeilingChoice';
import { isCadChoiceJob } from '../jobs/cadSolve';

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
  state: CadPreparationInputs & Pick<CadReturnState, 'ingestRecord' | 'needsIngest' | 'projectLineageId'>
    & Partial<Pick<CadReturnState, 'captureIngestIntent' | 'ingestStaleReason' | 'attemptedPreparationKey'>>,
  preparation: Pick<CadWorkspaceSnapshot, 'ingesting' | 'ingestError' | 'preparationIntent'> & Partial<Pick<CadWorkspaceSnapshot, 'ingestFailure'>>,
  request: CadModelRequest | null = null,
  /** The open WG design: a bundle naming it is WG's own design, linked. */
  openDesignId: string | null = null,
  /** A triangle-ceiling question asked in this window (`meshCeilingChoice`). */
  ceiling: MeshCeilingChoice | null = null,
  cadApplication = 'fusion360',
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
  // This window's own preparation failed, and the server classified it: its
  // sentence for it (the raw text of WG's own failure is developer detail).
  const localFailure = selected && currentPreparation && !preparation.ingesting && !!preparation.ingestError
    ? preparation.ingestFailure ?? null : null;
  const failureMessage = !selected ? null
    : localFailure ? preparation.ingestError
    : preparationPhase === 'prepare-failed' && serverFailed
      ? [request?.job?.cad_state?.message, request?.job?.cad_state?.next_step].filter(Boolean).join(' ') || null
    : preparationPhase === 'stopped' ? request?.message || null
    : null;
  // A ceiling question for this model: asked, or put off with Later.
  const meshChoice = selected && !!bundle && ceiling?.bundlePath === bundle.bundlePath
    && preparationPhase !== 'preparing' && preparationPhase !== 'prepared' ? ceiling : null;
  // A held request is the model's state until the user answers its pop-up:
  // only a real question (`isCadChoiceJob`). An engine hold is the solve
  // readiness gate, which Results describes.
  const heldRequest = selected && preparationPhase !== 'preparing' && request?.kind === 'waiting' && request.job
    && isCadChoiceJob(request.job) ? request.job : null;
  const phase: CadModelPhase = heldRequest || meshChoice ? 'waiting' : preparationPhase;
  // A record without a bundle (a restored run) names no application; it is
  // never assumed to be Fusion.
  const application = bundle?.bundleOrigin === 'onshape' ? 'Onshape'
    : bundle?.bundleOrigin === 'native' ? 'WG' : bundle ? 'Fusion' : 'CAD';
  return {
    phase, preparation: preparationPhase, heldRequest, meshChoice, failureMessage, selected, bundle, record, application,
    /** The raw text behind a recorded failure, for "Show developer details" only. */
    failureDiagnostic: localFailure?.diagnostic ?? null,
    /** Whether trying again can work, as the server said; null when it did not say. */
    failureRetry: localFailure ? localFailure.retry : null,
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
    preparesAutomatically: preparesAutomatically(state, openDesignId, cadApplication),
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
    useDocumentStore.getState().identity?.designId ?? null, meshCeilingChoice.getSnapshot(), preferencesStore.getSnapshot().cadApplication);
}

/** Subscribes to the fields the projection reads only, so a listing poll or a
 * status message does not re-render every consumer. */
export function useCadModelSelection(request: CadModelRequest | null = null) {
  const state = useCadReturnStore(useShallow(s => ({
    selectedBundle: s.selectedBundle, ingestRecord: s.ingestRecord, needsIngest: s.needsIngest,
    projectLineageId: s.projectLineageId, captureIngestIntent: s.captureIngestIntent,
    ingestStaleReason: s.ingestStaleReason, attemptedPreparationKey: s.attemptedPreparationKey,
    captureModelChoice: s.captureModelChoice, modelChoice: s.captureModelChoice(),
    meshSizes: s.meshSizes, skippedSourceIds: s.skippedSourceIds, areaDriftOverrides: s.areaDriftOverrides, symmetryMode: s.symmetryMode,
    // The ingest intent is a counter inside the store's closure; its
    // generation changes with the state the store publishes for it.
    ingestGeneration: s.captureIngestIntent(),
  })));
  const preparation = useStore(cadWorkspace, useShallow(s => ({
    ingesting: s.ingesting, ingestError: s.ingestError, ingestFailure: s.ingestFailure, preparationIntent: s.preparationIntent,
  })));
  const openDesignId = useDocumentStore(s => s.identity?.designId ?? null);
  const ceiling = useSyncExternalStore(meshCeilingChoice.subscribe, meshCeilingChoice.getSnapshot, meshCeilingChoice.getSnapshot);
  const { cadApplication } = usePreferences();
  return selectCadModelState(state, preparation, request, openDesignId, ceiling, cadApplication);
}

/** The selected CAD model, including a request held for the user. */
export function useCadModelState() {
  return useCadModelSelection(useCurrentModelStatus().status);
}

// The model's status wording has one owner: `cadModelStatusLine`
// (./cadModelStatusLine), which every surface reads.
