import type { CadReturnIngestRecord } from '../api/cadlink';
import { latestCadJobs, isFailedCadPreparation, isHeldCadJob } from '../jobs/cadSolve';
import { previewSocket } from '../api/previewSocket';
import { useSyncExternalStore } from 'react';
import { isActiveJobStatus, jobsSocket, type JobItem } from '../api/jobsSocket';
import { canLoadJobDesign, replaceWithJobDesign } from '../jobs/jobDesign';
import { designFingerprint, jobDesignFingerprint } from '../results/runCoherence';
import { cadModelSelection, useCadModelSelection } from '../stores/cadModelState';
import { currentDesignLoadSource, currentDocumentLoad, useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { useImportedModelStore } from '../stores/importedModel';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';
import { importedMeshStore, type ImportedMeshState } from '../viewport/importedMeshStore';

/** D4 MODEL identity: parametric design content or imported solve geometry SHA
 * (legacy ingestion fallback). It does not identify the complete solve request;
 * separate solver options, polar sampling and imported setup are outside it. */
export interface CurrentModel {
  kind: 'parametric' | 'file' | 'cad';
  key: string | null;
  lineage: string | null;
  label: string;
  viewportShowing: boolean;
  modelId?: string | null;
  ingestId?: string | null;
  design?: DesignDocument;
  manifestSha256?: string | null;
  /** An arrived Fusion request selected before it has a prepared mesh. */
  requestId?: string | null;
  /** An explicitly selected legacy run whose snapshot cannot be read. */
  unloadedRunId?: string;
}
export type RunVerdict = 'current' | 'earlier-version' | 'other-model' | 'model-not-shown';
export const RUN_VERDICT_MARKER = {
  'earlier-version': 'edited since',
  'other-model': 'Different CAD return',
  'model-not-shown': 'Run model not loaded',
};
export const RUN_VERDICT_SENTENCE = {
  'earlier-version': 'The design has been edited since this run was solved.',
  'other-model': "This run's CAD return differs from the one in the viewport.",
  'model-not-shown': "This run's model is not loaded in the viewport.",
};

// Explicitly opening an unreadable archive keeps its results accessible. Any
// workspace edit/navigation invalidates this exception by its store identities.
let unloaded: { id: string; design: DesignDocument; workspace: unknown; imported: unknown } | null = null;
export function showUnreadableRun(job: JobItem): void {
  unloaded = { id: job.id, design: useDesignStore.getState().design,
    workspace: workspaceModeStore.getSnapshot(), imported: useImportedModelStore.getState() };
}

/** Stored provenance wins over the anchor design retained by imported runs. */
export function modelKindForRun(job: Partial<Pick<JobItem, 'config_summary' | 'cad_state' | 'cad_intent' | 'design_availability'>>): CurrentModel['kind'] {
  const imported = job.config_summary?.geometry_type === 'imported' || job.config_summary?.formula_type === 'cad-import' || job.config_summary?.import_origin === 'mesh-file'
    || job.design_availability?.source === 'cad-import' || !!job.cad_state || !!job.cad_intent;
  return imported ? job.config_summary?.import_origin === 'mesh-file' ? 'file' : 'cad' : 'parametric';
}

/** The identity of the saved model, also used for exports explicitly scoped to a run. */
export function modelForRun(job: JobItem): CurrentModel {
  const kind = modelKindForRun(job);
  const imported = kind !== 'parametric';
  const ingestId = job.cad_source?.ingest_id ?? (typeof job.cad_intent?.bundle_path === 'string' && job.cad_intent.bundle_path.startsWith('ingest/') ? job.cad_intent.bundle_path.slice('ingest/'.length) : null) ?? job.cad_state?.preparation?.ingest_id ?? null;
  const modelId = job.cad_source?.model_id && job.cad_source.model_id !== 'unfiled' ? job.cad_source.model_id : null;
  const key = imported ? job.cad_source?.solve_model_sha256 || ingestId : jobDesignFingerprint(job);
  return {
    kind,
    key, lineage: imported ? modelId || ingestId : job.cad_source?.design_id ?? null,
    label: job.label || 'Run model', viewportShowing: true, ingestId,
    manifestSha256: job.cad_state?.snapshot?.manifest_sha256 ?? job.cad_source?.manifest_sha256,
    modelId,
    ...(key === null ? { unloadedRunId: job.id } : {}),
  };
}

/** Retained CAD artifacts are eligible only for the selected model. */
export function currentCadScenes(model: CurrentModel, viewport: ImportedMeshState = importedMeshStore.getSnapshot()) {
  return {
    cad: model.kind === 'cad' && model.ingestId && viewport.cad?.ingestId === model.ingestId ? viewport.cad : null,
    solver: model.kind === 'cad' && model.ingestId && viewport.cadSolver?.ingestId === model.ingestId ? viewport.cadSolver : null,
  };
}

/** The scene representing the current model; null uses the parametric preview. */
export function currentModelScene(model: CurrentModel, viewport: ImportedMeshState = importedMeshStore.getSnapshot()) {
  if (model.kind === 'file') return viewport.file && (!model.ingestId || viewport.file.ingestId === model.ingestId) ? viewport.file : null;
  if (model.kind === 'parametric') return viewport.showing === 'solver' ? viewport.solver : null;
  const { cad, solver } = currentCadScenes(model, viewport);
  return viewport.showing === 'cadSolver' ? solver ?? cad : cad;
}

export function currentModel(): CurrentModel {
  const workspace = workspaceModeStore.getSnapshot();
  const document = useDocumentStore.getState();
  const design = useDesignStore.getState().design;
  const imported = useImportedModelStore.getState();
  const viewport = importedMeshStore.getSnapshot();
  const unloadedRunId = unloaded && unloaded.design === design && unloaded.workspace === workspace
    && unloaded.imported === imported ? unloaded.id : undefined;
  const preview = previewSocket.getSnapshot();
  const parametricKey = designFingerprint(design);
  // An inactive preview lane has no retained mesh from a different document.
  // Once a lane is active, both document identity and revision must agree,
  // including after a failed load clears awaitingDocumentFrame.
  const previewShowing = !preview.awaitingDocumentFrame && (preview.frame
    ? (preview.frame.documentLoad === undefined || preview.frame.documentLoad === currentDocumentLoad())
      && preview.displayedRevision === useDesignStore.getState().designRevision
    : preview.connection === 'idle');
  if (workspace.mode === 'parametric') return {
    kind: 'parametric', key: parametricKey, lineage: document.identity?.designId ?? null,
    label: document.designName || 'Parametric model', design,
    viewportShowing: viewport.showing === 'solver' && viewport.solver
      ? viewport.solver.modelKey === parametricKey : previewShowing, unloadedRunId,
  };
  const file = workspace.importedInput === 'file';
  const cad = cadModelSelection();
  const record = file ? imported.input === 'file' ? imported.prepared : null : cad.record;
  const ingestId = record?.ingest_id ?? null;
  const sha = (record as { solve_model_sha256?: string } | null)?.solve_model_sha256;
  const registered = record?.model_id || (imported.input === workspace.importedInput && imported.prepared?.ingest_id === ingestId ? imported.modelId : null);
  const modelId = registered === 'unfiled' ? null : registered;
  const model: CurrentModel = { kind: file ? 'file' : 'cad', key: sha || ingestId, lineage: modelId || ingestId,
    label: file ? 'Mesh file' : cad.label, modelId, ingestId, unloadedRunId,
    requestId: file ? null : cad.bundle?.requestId,
    manifestSha256: record?.manifest_sha256,
    viewportShowing: false };
  return { ...model, viewportShowing: !!ingestId && currentModelScene(model, viewport) !== null };
}

export function useCurrentModel(): CurrentModel {
  useDesignStore(s => s.design);
  useDocumentStore(s => s.identity);
  useDocumentStore(s => s.designName);
  useCadModelSelection();
  useImportedModelStore();
  useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  useDesignStore(s => s.designRevision);
  useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot);
  useSyncExternalStore(importedMeshStore.subscribe, importedMeshStore.getSnapshot, importedMeshStore.getSnapshot);
  return currentModel();
}

/** The sole run-versus-viewport verdict. Legacy missing hashes use ingestion identity. */
export function runVerdict(job: JobItem, model: CurrentModel): RunVerdict {
  const run = modelForRun(job);
  if (model.unloadedRunId === job.id) return 'model-not-shown';
  if (run.kind !== model.kind) return 'other-model';
  const same = !!model.key && (run.key === model.key
    || ((!job.cad_source?.solve_model_sha256 || model.key === model.ingestId) && !!model.ingestId && run.ingestId === model.ingestId));
  if (same) return model.viewportShowing ? 'current' : 'model-not-shown';
  if (run.lineage && model.lineage && run.lineage === model.lineage) return 'earlier-version';
  // Old parametric archives have no document id; content remains authoritative.
  if (run.kind === 'parametric' && !run.lineage && run.key) return 'earlier-version';
  return 'other-model';
}

export interface ResultSelection {
  primary: string | null;
  overlays?: readonly string[];
  following?: boolean;
  /** Lifecycle evidence from the panel, not another display verdict. */
  resultsSeenFor?: string | null;
}
export interface ShownResult {
  job: JobItem | null;
  latest: JobItem | null;
  ids: string[];
  verdict: RunVerdict | null;
  modelNotLoaded: boolean;
  releaseSelection: boolean;
  canRender: boolean;
}

/** One chart/export selection. D1: earlier versions never supply charts. */
export function shownResult(model: CurrentModel, selection: ResultSelection, jobs: readonly JobItem[],
  provisional: Readonly<Record<string, unknown>> = {}): ShownResult {
  const drawable = (job: JobItem) => (job.status === 'complete' && job.has_results) || (isActiveJobStatus(job.status) && !!provisional[job.id]);
  const current = (job: JobItem) => runVerdict(job, model) === 'current';
  const latest = jobs.find(job => current(job) && drawable(job)) ?? null;
  const selected = jobs.find(job => job.id === selection.primary) ?? null;
  const verdict = selected ? runVerdict(selected, model) : null;
  const legacy = selected && model.unloadedRunId === selected.id;
  const cleaned = selected?.status === 'complete' && !selected.has_results && !!selected.results_discarded_at;
  const releaseSelection = !!cleaned && selection.resultsSeenFor === selected?.id;
  const job = releaseSelection ? latest : selected && ((current(selected) && drawable(selected)) || legacy || (cleaned && !selection.following)) ? selected : latest;
  const ids = job ? [job.id, ...(selection.overlays ?? []).filter(id => id !== job.id
    && jobs.some(candidate => candidate.id === id && current(candidate) && drawable(candidate)))] : [];
  return { job, latest, ids, verdict, modelNotLoaded: !!legacy, releaseSelection, canRender: !!job && drawable(job) };
}

/** Spatial artifacts require the shown run's model to actually be in the viewport.
 * The unreadable-archive exception permits standalone charts only. */
export function spatialResult(model: CurrentModel, selection: ResultSelection, jobs: readonly JobItem[],
  provisional: Readonly<Record<string, unknown>> = {}): JobItem | null {
  const { job } = shownResult(model, selection, jobs, provisional);
  return job && runVerdict(job, model) === 'current' ? job : null;
}

/** Existing job data only; slice 4 can supply readiness at this single seam. */
export function currentModelStatus(model: CurrentModel, jobs: readonly JobItem[], readiness?: string | null) {
  const candidates = [...latestCadJobs([...jobs]), ...jobs.filter(job => !job.cad_state)]
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''));
  const job = candidates.filter(job => job.cad_state?.reason !== 'superseded').find(job => runVerdict(job, { ...model, viewportShowing: true }) === 'current'
    || (model.kind === 'cad' && !model.key && model.requestId === job.id)
    // An unbound Fusion intent has no run identity yet; its captured return
    // manifest ties preparation/refusal to this source, never charts.
    || (model.kind === 'cad' && !job.cad_source && !job.has_results && !!model.manifestSha256
      && job.cad_state?.snapshot?.manifest_sha256 === model.manifestSha256));
  const state = job?.cad_state;
  if (job && isHeldCadJob(job)) return {
    kind: 'waiting' as const, job, message: state?.next_step || state?.message || 'Waiting for you',
  };
  // A failed preparation is told calmly, in the server's words, never as a refusal.
  if (job && isFailedCadPreparation(job)) return {
    kind: 'prepare-failed' as const, job,
    message: [state?.message, state?.next_step].filter(Boolean).join(' ') || 'WG could not prepare this model.',
  };
  if (state?.state === 'rejected' || state?.state === 'needs_user_input') return {
    kind: 'refused' as const, job, message: state.message || state.reason || job?.error_message || 'Solve refused.',
  };
  if (job?.status === 'preparing') return { kind: 'preparing' as const, job, message: job.stage_message || 'Preparing this model…' };
  if (job && isActiveJobStatus(job.status)) return { kind: 'running' as const, job, message: job.stage_message || 'Solving this model…' };
  if (job?.status === 'error') return { kind: 'failed' as const, job, message: job.error_message || 'The solve failed.' };
  if (job?.status === 'complete' && job.has_results) return { kind: 'solved' as const, job, message: 'Loading results…' };
  return { kind: 'never-solved' as const, job, message: readiness || 'This version of the model has not been solved.' };
}

/** The same request/run status for Results and the CAD Solve card. */
export function useCurrentModelStatus(record?: CadReturnIngestRecord) {
  const current = useCurrentModel();
  const model = record ? { ...current, kind: 'cad' as const, key: record.solve_model_sha256 || record.ingest_id,
    ingestId: record.ingest_id, manifestSha256: record.manifest_sha256 } : current;
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  return { model, status: currentModelStatus(model, jobs) };
}

export function runProvenanceMarker(job: Pick<JobItem, 'config_summary'>, mode: WorkspaceMode): string | null {
  if (job.config_summary.import_origin === 'mesh-file') return 'Mesh';
  if (job.config_summary.geometry_type === 'imported') return mode === 'cad' ? null : 'CAD';
  return mode === 'cad' ? 'Parametric' : null;
}
export function runContextMarker(job: JobItem, model: CurrentModel): string | null {
  const provenance = runProvenanceMarker(job, model.kind === 'parametric' ? 'parametric' : 'cad');
  const verdict = runVerdict(job, model);
  return provenance || (verdict === 'current' ? null : RUN_VERDICT_MARKER[verdict]);
}

/** The newest solved parametric run whose design can be put back on screen. */
export function latestSolvedParametricJob(jobs: JobItem[]): JobItem | null {
  const solvedAt = (job: JobItem) => Date.parse(job.completed_at ?? job.queued_at);
  return jobs
    .filter((job) => job.status === 'complete'
      && job.config_summary.geometry_type !== 'imported'
      && canLoadJobDesign(job))
    .reduce<JobItem | null>((latest, job) => (latest && solvedAt(latest) >= solvedAt(job) ? latest : job), null);
}

/**
 * Make a plain return to Parametric mode land on the user's parametric work.
 *
 * Opening a project from the CAD Link registry replaces the working design in
 * the store, so toggling back to Parametric would otherwise show that CAD
 * project's design as if it were the user's own. Restore the latest solved
 * parametric run's design instead — with history kept, so undo can still reach
 * the replaced document. When no CAD flow replaced the design (the common
 * case: CAD Link only showed an ingested mesh), the store already holds the
 * working design and nothing is touched.
 */
export function restoreParametricWorkingDesign(): void {
  if (currentDesignLoadSource() !== 'cad-project-switch') return;
  const job = latestSolvedParametricJob(jobsSocket.getSnapshot().jobs);
  if (job) replaceWithJobDesign(job, { keepHistory: true });
}
