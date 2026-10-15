import { isActiveJobStatus, type JobItem } from '../../api/jobsSocket';
import { compareSelection } from '../../api/results';
import { acknowledgeCadSolve, cadSolveClaim, latestCadJobs, rememberCadSolve } from '../../jobs/cadSolve';
import { RECEIPT_FIRST_CONNECTION_MS } from '../../stores/cadReceipts';
import { useCadReturnStore } from '../../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, subscribeRevision } from '../../stores/design';
import { subscribeSolveSettingsEdits } from '../../stores/solveSettingsEdits';
import { navigationGeneration } from '../workspaceNavigation';
import { solveAttention } from '../solveAttention';
import { restoreCadJobModel, type CadJobRestoreIntegration } from './restores';

interface FusionSolveDisplay extends Partial<CadJobRestoreIntegration> {
  isActive(): boolean;
  newestSendAt(): number;
  onIntent(): void;
}

/** Follow an explicit Fusion solve, including a running job first seen on opening
 * WG. Old history is not a new request; later user choices and Sends win.
 * Reuse history's exact-ingestion loader, never relax the result/model guard.
 */
export function createFusionSolveDisplay(display: FusionSolveDisplay, fetcher: typeof fetch = fetch,
  now = Date.now) {
  const since = now() - RECEIPT_FIRST_CONNECTION_MS;
  const observed = new Map<string, { navigation: number; edits: number; choice: number; document: ReturnType<typeof currentDocumentLoad> }>();
  const handled = new Set<string>();
  let newestSentAt = 0;
  let newestId: string | null = null;
  let latestJobs: readonly JobItem[] = [];
  let disposed = false;
  let edits = 0;
  const stopSettings = subscribeSolveSettingsEdits(() => { edits++; });
  const stopDesign = subscribeRevision(() => { edits++; });
  return {
    latestSentAt: () => newestSentAt,
    dispose: () => { disposed = true; stopSettings(); stopDesign(); },
    async observe(jobs: readonly JobItem[]): Promise<void> {
      latestJobs = jobs;
      if (disposed || !display.isActive()) return;
      const job = latestCadJobs([...jobs])[0];
      if (!job) { newestId = null; return; }
      if (handled.has(job.id)) return;
      const sentAt = Date.parse(job.created_at);
      if (!Number.isFinite(sentAt) || (!isActiveJobStatus(job.status) && sentAt < since)) return;
      if (jobs.some((other) => Date.parse(other.created_at) > sentAt)) return;
      const claim = cadSolveClaim(job);
      // Solve Again inherits its WG owner's claim. Its accepted websocket row
      // can beat the HTTP response and the coordinator's React effect.
      let parentId = job.parent_job_id;
      const visited = new Set<string>([job.id]);
      while (parentId && !visited.has(parentId)) {
        visited.add(parentId);
        const parent = jobs.find((item) => item.id === parentId);
        if (cadSolveClaim(parent ?? { id: parentId, client_request_id: null })?.sourceIngestId) return;
        parentId = parent?.parent_job_id ?? null;
      }
      // A WG press already owns its ingestion and its normal completion path.
      if (claim?.sourceIngestId || claim?.completionAcknowledged || job.cad_provenance?.manual_waiting) return;
      if (!observed.has(job.id)) {
        observed.set(job.id, { navigation: navigationGeneration(), edits, choice: useCadReturnStore.getState().captureModelChoice(),
          document: currentDocumentLoad() });
        solveAttention.noticeOperation(job.id);
      }
      if (sentAt < newestSentAt) return;
      newestSentAt = sentAt; newestId = job.id;
      display.onIntent();
      const intent = observed.get(job.id)!;
      const current = () => !disposed && display.isActive() && newestId === job.id
        && latestJobs.some((item) => item.id === job.id)
        && !latestJobs.some((item) => Date.parse(item.created_at) > sentAt)
        && display.newestSendAt() <= sentAt && navigationGeneration() === intent.navigation
        && edits === intent.edits && isCurrentDocumentLoad(intent.document);
      if (job.cad_state?.state !== 'accepted' || !job.cad_source?.ingest_id || job.run_number == null) return;
      handled.add(job.id);
      if (!current() || !useCadReturnStore.getState().isCurrentModelChoice(intent.choice)) return;
      // A first snapshot may already be accepted: there was no processing row
      // from which JobsCoordinator could have remembered this external press.
      rememberCadSolve(job.id, '');
      acknowledgeCadSolve(job, 'completion');
      const shown = await restoreCadJobModel(job, { ...display, isCurrent: current }, fetcher);
      if (!shown || !current()) return;
      compareSelection.awaitRun(job.id);
      if (job.client_request_id) solveAttention.followJob(job.client_request_id, job.id);
      solveAttention.bindRun(job.id, job.id);
    },
  };
}
