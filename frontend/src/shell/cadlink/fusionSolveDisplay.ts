import { isActiveJobStatus, type JobItem } from '../../api/jobsSocket';
import { reportClientError } from '../../api/diagnostics';
import { compareSelection } from '../../api/results';
import { acknowledgeCadSolve, cadSolveClaim, latestCadJobs, rememberCadSolve } from '../../jobs/cadSolve';
import { RECEIPT_FIRST_CONNECTION_MS } from '../../stores/cadReceipts';
import { useCadReturnStore } from '../../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, subscribeRevision } from '../../stores/design';
import { subscribeSolveSettingsEdits } from '../../stores/solveSettingsEdits';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { navigationGeneration, workspaceNavigation } from '../workspaceNavigation';
import { solveAttention } from '../solveAttention';
import { openImported, type ImportedDisplay } from '../../stores/importedModel';
import './restores';

interface FusionSolveDisplay extends ImportedDisplay {
  isActive(): boolean;
  newestSendAt(): number;
  onIntent(): void;
  reportDiagnostic?(jobId: string, reason: string, error?: unknown): void;
}

/** One developer-only hook, using the existing bounded local client log. */
export function reportFusionDisplayDiagnostic(jobId: string, reason: string, error?: unknown): void {
  void reportClientError({ source: 'fusionSolveDisplay', message: `Fusion display: ${reason}; job=${jobId}`,
    ...(error === undefined ? {} : { stack: error instanceof Error ? error.stack || error.message : String(error) }) });
}

type Intent = { navigation: number; edits: number; choice: number; document: ReturnType<typeof currentDocumentLoad> };
const DISPLAY_FAILURE = 'WG could not show the Fusion model. It tries again when the request updates or when you return to CAD or Results.';

/** Fusion Solve alone owns its request's model, from preparation through results.
 * A successful open is handled; a guard or failed read leaves it retryable.
 * Later edits/selections/documents always win. Navigation can be rearmed only
 * by an explicit return to CAD/Results, never by another jobs progress message.
 */
export function createFusionSolveDisplay(display: FusionSolveDisplay, fetcher: typeof fetch = fetch, now = Date.now) {
  const since = now() - RECEIPT_FIRST_CONNECTION_MS;
  const observed = new Map<string, Intent>();
  const handled = new Set<string>();
  const presented = new Set<string>();
  const reported = new Set<string>();
  const inFlight = new Map<string, Promise<void>>();
  let newestSentAt = 0;
  let newestId: string | null = null;
  let latestJobs: readonly JobItem[] = [];
  let disposed = false;
  let edits = 0;
  let opening: { current(): boolean; generation: number } | null = null;
  // The last time the request arrived or changed (a hold or a failure ends it).
  const changedAt = (job: JobItem) => Math.max(Date.parse(job.created_at), Date.parse(job.completed_at ?? '') || -Infinity);
  // Only a request that arrives or changes in this session (or within the
  // restart window) takes over the screen. An older held request is reached
  // through the request list, never by taking over at start.
  const eligible = (job: JobItem) => Number.isFinite(Date.parse(job.created_at))
    && (isActiveJobStatus(job.status) || changedAt(job) >= since);
  const diagnostic = (jobId: string, reason: string, error?: unknown) => {
    const key = `${jobId}:${reason === 'open-failed' ? 'failure' : 'skip'}`;
    if (reported.has(key)) return;
    reported.add(key);
    const report = display.reportDiagnostic ?? reportFusionDisplayDiagnostic;
    if (error === undefined) report(jobId, reason);
    else report(jobId, reason, error);
  };
  const cancelObsoleteOpen = () => {
    if (opening && !opening.current() && importedMeshStore.isCurrentGeneration(opening.generation)) {
      importedMeshStore.beginIntent();
    }
  };
  const stopSettings = subscribeSolveSettingsEdits(() => { edits++; cancelObsoleteOpen(); });
  const stopDesign = subscribeRevision(() => { edits++; cancelObsoleteOpen(); });
  const stopChoice = useCadReturnStore.subscribe(cancelObsoleteOpen);

  const resume = () => {
    if (disposed || !display.isActive()) return;
    const workspace = workspaceModeStore.getSnapshot();
    if (workspace.mode !== 'cad' || workspace.importedInput !== 'cad') return;
    // Only navigation is rearmed. The remaining captured evidence still has
    // to agree, so returning cannot discard a model choice or an edit.
    if (newestId) {
      const intent = observed.get(newestId);
      if (intent) intent.navigation = navigationGeneration();
    }
    void observe(latestJobs);
  };
  let workspace = workspaceModeStore.getSnapshot();
  const stopWorkspace = workspaceModeStore.subscribe(() => {
    const next = workspaceModeStore.getSnapshot();
    const returned = next.mode === 'cad' && next.importedInput === 'cad'
      && (workspace.mode !== 'cad' || workspace.importedInput !== 'cad');
    workspace = next;
    cancelObsoleteOpen();
    if (returned) queueMicrotask(resume);
  });
  let resultsVisible = workspaceNavigation.isVisible('results');
  let leftResultsAt = navigationGeneration();
  const stopNavigation = workspaceNavigation.subscribe(() => {
    cancelObsoleteOpen();
    const visible = workspaceNavigation.isVisible('results');
    const returned = !resultsVisible && visible && navigationGeneration() > leftResultsAt;
    if (resultsVisible && !visible) leftResultsAt = navigationGeneration();
    resultsVisible = visible;
    if (returned) queueMicrotask(() => {
      if (workspaceNavigation.isVisible('results')) resume();
    });
  });

  async function observe(jobs: readonly JobItem[]): Promise<void> {
    latestJobs = jobs;
    cancelObsoleteOpen();
    if (disposed) return;
    const job = latestCadJobs([...jobs])[0];
    if (!job) { newestId = null; return; }
    const sentAt = Date.parse(job.created_at);
    if (!eligible(job)) return;
    if (jobs.some((other) => Date.parse(other.created_at) > sentAt)) return;
    const claim = cadSolveClaim(job);
    let parentId = job.parent_job_id;
    const visited = new Set<string>([job.id]);
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      const parent = jobs.find((item) => item.id === parentId);
      if (cadSolveClaim(parent ?? { id: parentId, client_request_id: null })?.sourceIngestId) return;
      parentId = parent?.parent_job_id ?? null;
    }
    if (claim?.sourceIngestId || claim?.modelDisplayAcknowledged || job.cad_provenance?.manual_waiting) return;
    const first = !observed.has(job.id);
    if (first) {
      observed.set(job.id, { navigation: navigationGeneration(), edits,
        choice: useCadReturnStore.getState().captureModelChoice(), document: currentDocumentLoad() });
      solveAttention.noticeOperation(job.id);
    }
    if (sentAt < newestSentAt) return;
    newestSentAt = sentAt; newestId = job.id;
    display.onIntent();
    const intent = observed.get(job.id)!;
    const blocked = (): string | null => disposed ? 'disposed' : !display.isActive() ? 'inactive'
      : newestId !== job.id || !latestJobs.some((item) => item.id === job.id) ? 'superseded'
      : latestJobs.some((item) => Date.parse(item.created_at) > sentAt) || display.newestSendAt() > sentAt ? 'newer-request'
      : navigationGeneration() !== intent.navigation ? 'navigation'
      : edits !== intent.edits ? 'edited'
      : !isCurrentDocumentLoad(intent.document) ? 'document'
      : !useCadReturnStore.getState().isCurrentModelChoice(intent.choice) ? 'model-choice' : null;
    const current = () => blocked() === null;
    if (!current()) { diagnostic(job.id, blocked()!); return; }
    if (!presented.has(job.id)) {
      presented.add(job.id);
      display.enterCadWorkspace?.();
      // Select the request immediately so Results owns its Preparing progress,
      // even before the server can name a displayable preparation. This is a
      // request pointer, never a second browser-driven mesh preparation.
      if (typeof job.cad_intent?.bundle_path === 'string') {
        workspaceModeStore.setImportedInput('cad');
        const name = job.cad_state?.snapshot?.document_name || 'Fusion model';
        useCadReturnStore.getState().selectBundle({ name, documentName: name,
          bundlePath: job.cad_intent.bundle_path, modifiedAt: job.created_at, requestId: job.id,
          readable: false, sourceCount: null, instanceCount: null, sources: [],
        }, job.cad_state?.snapshot?.project_lineage_id, true, fetcher);
      }
      workspaceNavigation.activate('results');
    }
    const accepted = job.cad_state?.state === 'accepted' && job.run_number != null && !!job.cad_source?.ingest_id;
    const preparedId = accepted ? job.cad_source!.ingest_id : job.cad_state?.preparation?.ingest_id;
    if (!preparedId) return;
    const key = `${job.id}:${accepted ? 'run' : 'preparation'}:${preparedId}`;
    if (handled.has(key)) return;
    const pending = inFlight.get(job.id);
    if (pending) {
      await pending;
      // Acceptance may arrive while its preparing model is still loading.
      if (!disposed) await observe(latestJobs);
      return;
    }
    const action = (async () => {
      rememberCadSolve(job.id, '');
      const guardedDisplay = { ...display, isCurrent: current,
        // The request's progress belongs to Results. The shared history
        // opener must not front the geometry panel again at each transition.
        enterCadWorkspace: () => workspaceModeStore.setMode('cad'),
        failureMessage: (error: unknown) => { diagnostic(job.id, 'open-failed', error); return DISPLAY_FAILURE; },
        reportStatus: (message: string) => display.reportStatus?.(message.startsWith('Cannot show ')
          ? DISPLAY_FAILURE : message) };
      const loading = openImported(accepted
        ? { kind: 'run', job, display: guardedDisplay }
        : { kind: 'cad-preparation', job, preparedId, display: guardedDisplay }, fetcher);
      // The shared opener takes its own selection intent synchronously. From
      // this point only a subsequent person-driven choice can supersede it.
      intent.choice = useCadReturnStore.getState().captureModelChoice();
      const ownOpen = { current, generation: importedMeshStore.captureGeneration() };
      opening = ownOpen;
      let shown = false;
      try { shown = await loading; }
      catch {
        diagnostic(job.id, 'open-failed');
        if (current()) display.reportStatus?.(DISPLAY_FAILURE);
        return;
      } finally { if (opening === ownOpen) opening = null; }
      if (!shown || !current()) { diagnostic(job.id, blocked() ?? 'not-shown'); return; }
      handled.add(key);
      if (!accepted) return;
      acknowledgeCadSolve(job, 'completion');
      acknowledgeCadSolve(job, 'display');
      compareSelection.awaitRun(job.id);
      if (job.client_request_id) solveAttention.followJob(job.client_request_id, job.id);
      solveAttention.bindRun(job.id, job.id);
    })();
    inFlight.set(job.id, action);
    try { await action; } finally { inFlight.delete(job.id); }
  }
  return {
    latestSentAt: () => newestSentAt, observe, resume,
    ownsReturn: (path: string, jobs: readonly JobItem[], initial = false) => latestCadJobs([...jobs]).some((job) =>
      job.cad_intent?.bundle_path === path && (!initial || (eligible(job)
        && !cadSolveClaim(job)?.sourceIngestId && !cadSolveClaim(job)?.modelDisplayAcknowledged
        && !job.cad_provenance?.manual_waiting))),
    dispose: () => {
      disposed = true; cancelObsoleteOpen(); stopSettings(); stopDesign(); stopChoice(); stopWorkspace(); stopNavigation();
    },
  };
}
