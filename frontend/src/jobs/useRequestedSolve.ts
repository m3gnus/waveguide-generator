import { useCallback, useEffect, useRef, useState } from 'react';
import { currentModel, currentModelStatus, type CurrentModel } from '../model/currentModel';
import { jobsSocket } from '../api/jobsSocket';
import { previewSocket } from '../api/previewSocket';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { currentDocumentLoad, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useImportedModelStore } from '../stores/importedModel';
import { useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { useMeshImportStore, fileMeshSetup } from '../stores/meshImport';
import { subscribeSolveSettingsEdits } from '../stores/solveSettingsEdits';
import { buildImportedSubmission } from './importedSubmission';

export interface ModelSolveContinuation {
  model: CurrentModel;
  engine: string;
  mode: string;
  setupRevision: string;
  signal: AbortSignal;
  /** Set synchronously at the POST boundary, before job events can arrive. */
  submitted?: boolean;
}
export interface HeldSolveContinuation extends ModelSolveContinuation { jobId: string }
export type RequestedSolve = ModelSolveContinuation & {
  controller: AbortController;
  jobId?: string;
  preparationEngine?: string;
  queued: boolean;
  waiting: boolean;
  preparing: boolean;
  continuing: boolean;
};

function solveInputs() {
  const model = currentModel();
  const options = model.kind === 'parametric' ? useSolveOptionsStore.getState() : useImportedSolveOptionsStore.getState();
  const imported = useImportedModelStore.getState();
  let setup: unknown = null;
  // Reuse the submission builders: setup identity includes retained physics,
  // not just the subset of settings the current controls can express.
  try {
    setup = model.kind === 'file' ? fileMeshSetup()
      : model.kind === 'cad' ? buildImportedSubmission(useCadReturnStore.getState()) : null;
  } catch { /* An incomplete edit is also caught by the control snapshot/edit signal. */ }
  const frame = model.kind === 'cad' && model.ingestId ? useCadSolverFrameStore.getState().frames[model.ingestId] : null;
  return { model, engine: options.engine, mode: workspaceModeStore.getSnapshot().mode,
    setupRevision: JSON.stringify({ options, setup, framePick: frame?.picked ? frame.axis : null,
      revision: model.kind === 'parametric' ? [currentDocumentLoad(), useDesignStore.getState().designRevision]
        : [imported.restoredRevisionId, imported.setup, imported.setupEditBase] }) };
}

export function captureSolveInputs(): Omit<ModelSolveContinuation, 'signal'> { return solveInputs(); }

/** The one live-input guard used by observers and by every delayed POST. */
export function solveCancellationReason(request: ModelSolveContinuation & { jobId?: string }): string | null {
  const inputs = solveInputs();
  const model = inputs.model;
  if (model.key !== request.model.key || model.kind !== request.model.kind || model.ingestId !== request.model.ingestId
    || inputs.mode !== request.mode) return 'Prepare and solve was cancelled because the model on screen changed.';
  if (!model.viewportShowing) return 'Prepare and solve was cancelled because the model left the viewport.';
  if (inputs.engine !== request.engine) return 'Prepare and solve was cancelled because the selected engine changed.';
  if (inputs.setupRevision !== request.setupRevision) return 'Prepare and solve was cancelled because the solve setup changed.';
  if (request.jobId) {
    const job = currentModelStatus(model, jobsSocket.getSnapshot().jobs).job;
    if (job?.id !== request.jobId || job.status !== 'error' || job.cancellation_requested
      || job.cad_state?.state !== 'needs_user_input' || job.cad_state.reason !== 'engine_unavailable') {
      return 'Prepare and solve was cancelled because the waiting job changed.';
    }
  }
  return null;
}

export function solveStillCurrent(request: ModelSolveContinuation): boolean {
  return !request.signal.aborted && solveCancellationReason(request) === null;
}

/** One owner from the press (or held Fusion intention) through the POST. */
export function useRequestedSolve(refresh: () => void | Promise<void>, reportNotice: (message: string | null) => void,
  releaseAttention: () => void) {
  const [request, setRequest] = useState<RequestedSolve | null>(null);
  const current = useRef<RequestedSolve | null>(null);
  const report = useRef(reportNotice);
  report.current = reportNotice;
  const release = useRef(releaseAttention);
  release.current = releaseAttention;
  const cancel = useCallback((reason?: string) => {
    const held = current.current;
    if (!held) return;
    held.controller.abort();
    current.current = null;
    setRequest(null);
    if (!held.submitted) release.current();
    if (!held.submitted && reason) report.current(reason);
  }, []);
  const update = useCallback((token: RequestedSolve, patch: Partial<RequestedSolve>) => {
    if (current.current !== token) return;
    Object.assign(token, patch);
    setRequest({ ...token });
  }, []);
  const begin = useCallback((jobId?: string) => {
    if (current.current) return current.current;
    const controller = new AbortController();
    const token: RequestedSolve = { ...captureSolveInputs(), jobId, controller, signal: controller.signal,
      queued: false, waiting: false, preparing: false, continuing: false };
    current.current = token;
    setRequest({ ...token });
    report.current(null);
    return token;
  }, []);
  const finish = useCallback((token: RequestedSolve) => { if (current.current === token) cancel(); }, [cancel]);
  const beforePost = useCallback((token: ModelSolveContinuation | null): boolean => {
    if (!token) return true;
    if (!solveStillCurrent(token)) return false;
    token.submitted = true;
    if (current.current === token) setRequest({ ...current.current });
    return true;
  }, []);
  useEffect(() => {
    const validate = () => {
      const token = current.current;
      if (!token || token.submitted) return;
      const reason = solveCancellationReason(token);
      if (reason) cancel(token.jobId ? reason : undefined);
    };
    // Store observers latch leave/return and change/undo inside one React batch.
    const stops = [jobsSocket.subscribe(validate), previewSocket.subscribe(validate), useCadReturnStore.subscribe(validate), useCadSolverFrameStore.subscribe(validate),
      workspaceModeStore.subscribe(validate), importedMeshStore.subscribe(validate), useDesignStore.subscribe(validate),
      useDocumentStore.subscribe(validate), useImportedModelStore.subscribe(validate), useMeshImportStore.subscribe(validate),
      useSolveOptionsStore.subscribe(validate), useImportedSolveOptionsStore.subscribe(validate),
      subscribeSolveSettingsEdits(edit => {
        const token = current.current;
        if (!token || token.submitted) return;
        const parametric = token.model.kind === 'parametric';
        if ((edit?.source === 'solveOptions') !== parametric) return;
        cancel(token.jobId ? 'Prepare and solve was cancelled because the solve setup changed.' : undefined);
      })];
    return () => {
      stops.forEach(stop => stop());
      if (current.current && !current.current.submitted) release.current();
      current.current?.controller.abort();
      current.current = null;
    };
  }, [cancel]);
  const observing = !!request && !request.submitted && (request.queued || request.waiting);
  useEffect(() => {
    if (!observing) return;
    let refreshing = false;
    const observe = async () => {
      if (refreshing) return;
      refreshing = true;
      try { await refresh(); } catch { /* Keep observing through transient network failures. */ }
      finally { refreshing = false; }
    };
    void observe();
    const timer = setInterval(() => { void observe(); }, 1000);
    return () => clearInterval(timer);
  }, [observing, refresh]);
  return { request, current, begin, update, cancel, finish, beforePost };
}
export type RequestedSolveOwner = ReturnType<typeof useRequestedSolve>;
