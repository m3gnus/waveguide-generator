import { CadLinkApiError, getIngest, type CadReturnIngestRecord } from '../../api/cadlink';
import type { JobItem } from '../../api/jobsSocket';
import { useCadReturnStore } from '../../stores/cadReturn';
import { useCadSolverFrameStore } from '../../stores/cadSolverFrame';
import { importedMeshStore } from '../../viewport/importedMeshStore';

/**
 * Put the model a CAD solve prepared on screen, once the user's solve claims its run.
 *
 * A CAD solve does not solve the ingestion on screen: the backend prepares
 * the return again, in the solver frame and domain chosen at Solve
 * (`server/cadlink/preparation.py`), and the run names that preparation. When
 * Solve confirmed an axis other than the one the screen was meshed along --
 * "Prepared along +z; Solve prepares it again along -y" -- the run's solve
 * model differs from the one on screen, and Results rightly called it a
 * different CAD return while the viewport went on showing a frame the project
 * no longer solves in. The run's identity is right; the screen was stale. So
 * the run the user's Solve claims hands its prepared ingestion to the
 * viewport, as a fresh ingest of the same return would after that Solve.
 *
 * Only ever the same return, still on screen as the solve left it: a newer
 * return, an ingest in flight, settings edited since, or an axis picked on
 * the frame card since keep what the user put there, and the run keeps its
 * marker.
 */
export interface SolvedModelClaim {
  /** The snapshot the solve was of. */
  manifestSha256: string | null;
  /** The ingestion on screen when Solve was pressed in WG; null for a solve
   * Fusion asked for, which names only its snapshot. */
  sourceIngestId: string | null;
}

export interface SolvedModelDisplay {
  showIngestedMesh(
    record: CadReturnIngestRecord,
    name: string,
    onNotice: ((notice: string) => void) | undefined,
    fetcher: typeof fetch,
    generation: number,
  ): Promise<void>;
  reportViewportNotice?(message: string | null): void;
}

export type SolvedModelOutcome = 'adopted' | 'already-shown' | 'declined';

/** Reads of the prepared record a claim may make: a dropped connection is retried
 * on a later jobs message, a record that does not exist is not. */
const ATTEMPTS = 3;

const claims = new Map<string, SolvedModelClaim & { attempts: number }>();
const listeners = new Set<() => void>();

export const solvedCadModels = {
  /** The run a CAD solve of the user's was submitted as. */
  claim(jobId: string, claim: SolvedModelClaim): void {
    claims.set(jobId, { ...claim, attempts: 0 });
    listeners.forEach((listener) => listener());
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  /** Adopt each claimed run whose row now names its prepared ingestion. Once per claim. */
  settle(jobs: readonly JobItem[], display: SolvedModelDisplay, fetcher: typeof fetch = fetch): void {
    for (const [jobId, claim] of claims) {
      const job = jobs.find((item) => item.id === jobId);
      if (!job?.cad_source?.ingest_id) continue;
      claims.delete(jobId);
      void adoptSolvedCadModel(job, claim, display, fetcher).catch((reason: unknown) => {
        const missing = reason instanceof CadLinkApiError && reason.status === 404;
        if (!missing && claim.attempts + 1 < ATTEMPTS && !claims.has(jobId)) {
          claims.set(jobId, { ...claim, attempts: claim.attempts + 1 });
        }
      });
    }
  },
};

/** Tests only. */
export function resetSolvedCadModelsForTests(): void {
  claims.clear();
  listeners.clear();
}

function shownReturn(job: JobItem, claim: SolvedModelClaim, preparedId: string): CadReturnIngestRecord | null {
  const state = useCadReturnStore.getState();
  const record = state.ingestRecord;
  if (!record || state.needsIngest || !state.isIngestSettled()) return null;
  if (record.ingest_id === preparedId) return record;
  if (claim.sourceIngestId !== null && record.ingest_id !== claim.sourceIngestId) return null;
  if (!claim.manifestSha256 || record.manifest_sha256 !== claim.manifestSha256) return null;
  // The card keys an axis the user picked by the ingestion; another one would
  // drop a pick the run was not solved along.
  const frame = useCadSolverFrameStore.getState().frames[record.ingest_id];
  if (frame?.picked && frame.axis !== (job.cad_provenance?.frame?.axis ?? null)) return null;
  return record;
}

export async function adoptSolvedCadModel(
  job: JobItem,
  claim: SolvedModelClaim,
  display: SolvedModelDisplay,
  fetcher: typeof fetch = fetch,
): Promise<SolvedModelOutcome> {
  const preparedId = job.cad_source?.ingest_id;
  if (job.config_summary?.geometry_type !== 'imported' || !preparedId) return 'declined';
  if (job.cad_source?.manifest_sha256 && job.cad_source.manifest_sha256 !== claim.manifestSha256) return 'declined';
  const shown = shownReturn(job, claim, preparedId);
  if (!shown) return 'declined';
  if (shown.ingest_id === preparedId) return 'already-shown';
  // Read without taking the intent: an ingest the user starts meanwhile wins.
  const prepared = await getIngest(preparedId, fetcher);
  const state = useCadReturnStore.getState();
  if (state.ingestRecord !== shown || shownReturn(job, claim, preparedId) !== shown) return 'declined';
  if (prepared.ingest_id !== preparedId || prepared.manifest_sha256 !== shown.manifest_sha256) return 'declined';
  const viewportGeneration = importedMeshStore.beginIntent();
  if (!state.applyIngest(prepared, state.beginIngestIntent())) return 'declined';
  const bundle = state.selectedBundle;
  const name = bundle?.documentName || bundle?.name || job.cad_source?.document_name || job.label || 'CAD model';
  await display.showIngestedMesh(prepared, name, display.reportViewportNotice, fetcher, viewportGeneration);
  return 'adopted';
}
