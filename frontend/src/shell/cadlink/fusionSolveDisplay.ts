import { listReturns, type CadReturnBundle } from '../../api/cadlink';
import { getCadOperation } from '../../api/cadOperations';
import { openLinkedJob } from '../../jobs/jobDeepLink';
import { jobsSocket, isActiveJobStatus, type CadShowRequest, type JobItem } from '../../api/jobsSocket';
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
import historyText from '../../api/requestHistoryText.json';
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

type Intent = { sendAt?: number; navigation: number; edits: number; choice: number; document: ReturnType<typeof currentDocumentLoad> };
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
  let explicitId: string | null = null;
  let explicitAt = 0;
  let explicitOperationId: string | null = null;
  let showGeneration = 0;
  let unavailableGeneration = -1;
  const reportUnavailable = () => {
    if (unavailableGeneration === showGeneration) return;
    unavailableGeneration = showGeneration;
    display.reportStatus?.(display.isActive() ? historyText.unavailable : historyText.outsideFusion);
  };
  let explicitJob: JobItem | null = null;
  let explicitIntent: Intent | null = null;
  let edits = 0;
  let opening: { current(): boolean; generation: number } | null = null;
  // The last time the request arrived or changed (a hold or a failure ends it).
  const changedAt = (job: JobItem) => Math.max(Date.parse(job.created_at), Date.parse(job.completed_at ?? '') || -Infinity);
  // Only a request that arrives or changes in this session (or within the
  // restart window) takes over the screen. An older held request is reached
  // through the request list, never by taking over at start.
  const eligible = (job: JobItem) => Number.isFinite(Date.parse(job.created_at))
    && (isActiveJobStatus(job.status) || changedAt(job) >= since);
  const canFollow = (job: JobItem, jobs: readonly JobItem[]) => {
    if (!eligible(job)) return false;
    const claim = cadSolveClaim(job);
    let parentId = job.parent_job_id;
    const visited = new Set<string>([job.id]);
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      const parent = jobs.find((item) => item.id === parentId);
      if (cadSolveClaim(parent ?? { id: parentId, client_request_id: null })?.sourceIngestId) return false;
      parentId = parent?.parent_job_id ?? null;
    }
    if (claim?.sourceIngestId || claim?.modelDisplayAcknowledged || job.cad_provenance?.manual_waiting) return false;
    return true;
  };
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

  const explicitCurrent = () => explicitIntent !== null && !disposed && display.isActive()
    && navigationGeneration() === explicitIntent.navigation && edits === explicitIntent.edits
    && display.newestSendAt() <= (explicitIntent.sendAt ?? 0) && isCurrentDocumentLoad(explicitIntent.document)
    && useCadReturnStore.getState().isCurrentModelChoice(explicitIntent.choice);

  async function observe(jobs: readonly JobItem[]): Promise<void> {
    latestJobs = jobs;
    cancelObsoleteOpen();
    if (disposed) return;
    // A newly pressed Solve releases history; paging, old jobs and the
    // selected request's own retries do not. Use creation, never completion.
    const newerSolve = explicitIntent && latestCadJobs([...jobs]).some((job) => {
      if (Date.parse(job.created_at) <= explicitAt || job.id === explicitId) return false;
      if (job.cad_state?.operation_id === explicitOperationId
        || job.client_request_id === `cad-solve:${explicitOperationId}`) return false;
      let parentId = job.parent_job_id;
      const visited = new Set<string>();
      while (parentId && !visited.has(parentId)) {
        if (parentId === explicitId) return false;
        visited.add(parentId);
        parentId = jobs.find((item) => item.id === parentId)?.parent_job_id ?? null;
      }
      return true;
    });
    if (explicitIntent && (!explicitCurrent() || newerSolve)) {
      explicitId = null; explicitJob = null; explicitIntent = null;
      showGeneration++; cancelObsoleteOpen();
    }
    if (explicitIntent && !explicitId) return; // an explicit Send owns its return too
    if (explicitId) explicitJob = jobs.find((item) => item.id === explicitId) ?? explicitJob;
    const job = explicitId ? explicitJob : latestCadJobs([...jobs])[0];
    if (!job) { newestId = null; return; }
    const sentAt = Date.parse(job.created_at);
    const explicit = explicitId === job.id;
    const generation = showGeneration;
    if (!explicit && !canFollow(job, jobs)) return;
    if (!explicit && jobs.some((other) => Date.parse(other.created_at) > sentAt)) return;
    const first = !observed.has(job.id);
    if (first) {
      observed.set(job.id, { navigation: navigationGeneration(), edits,
        choice: useCadReturnStore.getState().captureModelChoice(), document: currentDocumentLoad() });
      solveAttention.noticeOperation(job.id);
    }
    if (!explicit && sentAt < newestSentAt) return;
    newestSentAt = Math.max(newestSentAt, sentAt); newestId = job.id;
    display.onIntent();
    const intent = observed.get(job.id)!;
    const blocked = (): string | null => disposed ? 'disposed' : !display.isActive() ? 'inactive'
      : (explicit && generation !== showGeneration) || newestId !== job.id || (!explicit && !latestJobs.some((item) => item.id === job.id)) ? 'superseded'
      : (explicit ? display.newestSendAt() > (intent.sendAt ?? 0)
        : latestJobs.some((item) => Date.parse(item.created_at) > sentAt) || display.newestSendAt() > sentAt) ? 'newer-request'
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
      if (typeof job.cad_return?.bundle_path === 'string') {
        workspaceModeStore.setImportedInput('cad');
        const name = job.cad_state?.snapshot?.document_name || 'Fusion model';
        useCadReturnStore.getState().selectBundle({ name, documentName: name,
          bundlePath: job.cad_return.bundle_path, returnId: job.cad_return.return_id, modifiedAt: job.created_at, requestId: job.id,
          readable: false, sourceCount: null, instanceCount: null, sources: [],
        }, job.cad_state?.snapshot?.project_lineage_id, true, fetcher);
      }
      workspaceNavigation.activate('results');
      if (explicit) {
        intent.navigation = navigationGeneration();
        intent.choice = useCadReturnStore.getState().captureModelChoice();
      }
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
        failureMessage: (error: unknown) => { diagnostic(job.id, 'open-failed', error); return explicit ? historyText.unavailable : DISPLAY_FAILURE; },
        reportStatus: (message: string) => {
          if (explicit && (message === historyText.unavailable || message.startsWith('Cannot show ') || message.startsWith('No run with id '))) reportUnavailable();
          else display.reportStatus?.(message.startsWith('Cannot show ') ? DISPLAY_FAILURE : message);
        } };
      let loading: Promise<boolean>;
      if (explicit && accepted) {
        // The ?job= path selects existing runs; the owner supplies its guarded
        // model opener so a later user navigation still cancels this load.
        loading = Promise.resolve(false);
        openLinkedJob(job.id, [job], reportUnavailable, (selected) => {
          loading = openImported({ kind: 'run', job: selected, display: guardedDisplay }, fetcher);
          return loading;
        });
      } else {
        loading = openImported(accepted
          ? { kind: 'run', job, display: guardedDisplay }
          : { kind: 'cad-preparation', job, preparedId, display: guardedDisplay }, fetcher);
      }
      // The shared opener takes its own selection intent synchronously. From
      // this point only a subsequent person-driven choice can supersede it.
      intent.choice = useCadReturnStore.getState().captureModelChoice();
      const ownOpen = { current, generation: importedMeshStore.captureGeneration() };
      opening = ownOpen;
      let shown = false;
      try { shown = await loading; }
      catch {
        diagnostic(job.id, 'open-failed');
        if (current()) { if (explicit) reportUnavailable(); else display.reportStatus?.(DISPLAY_FAILURE); }
        return;
      } finally { if (opening === ownOpen) opening = null; }
      if (!shown || !current()) { diagnostic(job.id, blocked() ?? 'not-shown'); return; }
      handled.add(key);
      if (!accepted) return;
      acknowledgeCadSolve(job, 'completion');
      acknowledgeCadSolve(job, 'display');
      if (!explicit) compareSelection.awaitRun(job.id);
      if (job.client_request_id) solveAttention.followJob(job.client_request_id, job.id);
      solveAttention.bindRun(job.id, job.id, null);
    })();
    inFlight.set(job.id, action);
    try { await action; } finally { inFlight.delete(job.id); }
  }
  async function showRequest(request: CadShowRequest): Promise<void> {
    const generation = ++showGeneration;
    explicitAt = now(); explicitOperationId = request.operationId;
    cancelObsoleteOpen();
    if (!display.isActive()) { reportUnavailable(); return; }
    const intent: Intent = { sendAt: display.newestSendAt(), navigation: navigationGeneration(), edits,
      choice: useCadReturnStore.getState().captureModelChoice(), document: currentDocumentLoad() };
    const current = () => !disposed && display.isActive() && generation === showGeneration
      && display.newestSendAt() <= (intent.sendAt ?? 0)
      && navigationGeneration() === intent.navigation && edits === intent.edits
      && isCurrentDocumentLoad(intent.document) && useCadReturnStore.getState().isCurrentModelChoice(intent.choice);
    try {
      let job = request.jobId ? latestJobs.find((item) => item.id === request.jobId)
        : latestCadJobs([...latestJobs]).find((item) => item.cad_state?.operation_id === request.operationId
          || item.client_request_id === `cad-solve:${request.operationId}`);
      if (!job && request.jobId) {
        const response = await fetcher(`/api/status/${encodeURIComponent(request.jobId)}`);
        if (!current()) return;
        if (!response.ok) { reportUnavailable(); return; }
        const found = await response.json() as JobItem;
        if (found.id !== request.jobId) { reportUnavailable(); return; }
        job = found;
      }
      if (!current()) return;
      if (!job) {
        // Send has no solve job. Select its existing return through CAD's
        // ordinary selection path; never create a browser preparation here.
        const operation = await getCadOperation(request.operationId, fetcher);
        if (!current()) return;
        if (operation.kind !== 'receive_snapshot') { reportUnavailable(); return; }
        const listing = await listReturns(fetcher);
        if (!current()) return;
        const bundle = listing.items.find((item) => item.bundlePath === operation.snapshot?.bundlePath);
        if (!bundle) { reportUnavailable(); return; }
        display.onIntent(); display.enterCadWorkspace?.();
        useCadReturnStore.getState().selectBundle(bundle, operation.snapshot?.projectLineageId, true, fetcher);
        workspaceNavigation.activate('results');
        intent.navigation = navigationGeneration();
        intent.choice = useCadReturnStore.getState().captureModelChoice();
        explicitIntent = intent; explicitId = null; explicitJob = null;
        return;
      }
      explicitId = job.id; explicitJob = job; explicitIntent = intent;
      observed.set(job.id, intent); presented.delete(job.id);
      for (const key of handled) if (key.startsWith(`${job.id}:`)) handled.delete(key);
      await observe([job, ...latestJobs.filter((item) => item.id !== job.id)]);
    } catch (error) {
      if (current()) reportUnavailable();
      diagnostic(request.jobId ?? request.operationId, 'open-failed', error);
    }
  }
  const stopShowRequest = jobsSocket.subscribeCadShowRequest((request) => { void showRequest(request); });
  return {
    latestSentAt: () => newestSentAt, observe, resume, showRequest,
    // Hold only returns that predate the show. A new Send must stay eligible
    // even when its receipt arrives before discovery sees the return.
    ownsReturn: (bundle: CadReturnBundle, jobs: readonly JobItem[]) =>
      (explicitCurrent() && Date.parse(bundle.modifiedAt) <= explicitAt) || latestCadJobs([...jobs]).some((job) =>
      job.cad_return?.bundle_path === bundle.bundlePath
      && (bundle.returnId && job.cad_return.return_id ? job.cad_return.return_id === bundle.returnId
        : Date.parse(bundle.modifiedAt) <= Date.parse(job.created_at))
      && (job.id === explicitId || canFollow(job, jobs))),
    dispose: () => {
      disposed = true; stopShowRequest(); cancelObsoleteOpen(); stopSettings(); stopDesign(); stopChoice(); stopWorkspace(); stopNavigation();
    },
  };
}
