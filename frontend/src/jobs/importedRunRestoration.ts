import { create } from 'zustand';
import type { JobItem } from '../api/jobsSocket';
import { readImportedJobRequest } from '../api/jobRequests';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { workspaceModeStore } from '../stores/workspaceMode';
import { subscribeSolveSettingsEdits } from '../stores/solveSettingsEdits';
import type { ImportedSolveSubmission } from './actions';
import type { ImportedSetup, PreparedMeshIdentity } from './importedSetup';

interface Restoration {
  generation: number;
  input: 'file' | 'cad';
  ingestId: string;
  phase: 'loading' | 'ready' | 'failed';
  error: string | null;
  submission: ImportedSolveSubmission | null;
  /** The setup revision the run recorded: an unedited Solve sends it back. */
  setupRevisionId?: string | null;
  isCurrent?: () => boolean;
}
export const useImportedRunRestoration = create<{ restoration: Restoration | null }>(() => ({ restoration: null }));

/** Both producers restore one frozen request through the same guarded transaction. */
export async function restoreImportedRun(
  job: JobItem,
  adapter: {
    input: 'file' | 'cad';
    invalidate: () => void;
    isCurrent?: () => boolean;
    ownsSetup?: () => boolean;
    restore: (submission: ImportedSolveSubmission, generation: number, current: () => boolean) => Promise<boolean>;
  },
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  const generation = importedMeshStore.beginIntent();
  const ingestId = job.cad_source?.ingest_id ?? '';
  const restoration: Restoration = { generation, input: adapter.input, ingestId,
    phase: 'loading', error: null, submission: null, setupRevisionId: recordedSetupRevision(job),
    isCurrent: adapter.isCurrent };
  useImportedRunRestoration.setState({ restoration });
  adapter.invalidate();
  const current = () => useImportedRunRestoration.getState().restoration?.generation === generation
    && useImportedRunRestoration.getState().restoration?.phase === 'loading'
    && importedMeshStore.isCurrentGeneration(generation) && adapter.isCurrent?.() !== false;
  try {
    if (!ingestId) throw new Error('This run has no retained mesh identity. Import the geometry again.');
    const effective = await readImportedJobRequest(job.id, fetcher);
    if (!current()) return false;
    const submission = { geometry: effective?.geometry, options: effective?.options };
    if (submission.geometry?.type !== 'imported' || submission.geometry.ingest_id !== ingestId
      || !submission.options || !Array.isArray(submission.geometry.drive_channels)) {
      throw new Error('This run’s saved request does not match its retained mesh. Select the run again.');
    }
    if (!await adapter.restore(submission, generation, current)) {
      if (!current()) return false;
      throw new Error('This run’s retained mesh artifacts are no longer available. Import the geometry again.');
    }
    if (!current()) return false;
    useImportedRunRestoration.setState({ restoration: { ...restoration, phase: 'ready',
      submission: structuredClone(submission), isCurrent: adapter.ownsSetup ?? adapter.isCurrent } });
    return true;
  } catch (error) {
    if (!current()) return false;
    const message = error instanceof Error ? error.message : String(error);
    useImportedRunRestoration.setState({ restoration: { ...restoration, phase: 'failed', error: message } });
    throw error;
  }
}

/** The setup revision a run says it was solved with, or null for one that recorded none. */
export function recordedSetupRevision(job: JobItem): string | null {
  return job.cad_source?.setup_revision_id ?? job.cad_provenance?.setup?.revision_id ?? null;
}

export function assertRestoredIdentity(record: PreparedMeshIdentity, submission: ImportedSolveSubmission): void {
  if (submission.geometry.ingest_id !== record.ingest_id
    || submission.geometry.manifest_sha256 !== record.manifest_sha256
    || submission.geometry.artifact_sha256 !== record.artifact_sha256) {
    throw new Error('This run’s saved request does not match its retained mesh. Select the run again.');
  }
}

export function importedRunRestoreBlocker(input: 'file' | 'cad'): string | null {
  const state = useImportedRunRestoration.getState().restoration;
  if (!state || state.input !== input || state.isCurrent?.() === false || !importedMeshStore.isCurrentGeneration(state.generation)) return null;
  return state.phase === 'loading' ? 'Wait for this run’s retained setup to finish loading.'
    : state.phase === 'failed' ? state.error : null;
}

/** The CAD rail is a projection; fields it cannot express remain in the frozen request. */
export function restoredCadSubmission(ingestId?: string): ImportedSolveSubmission | null {
  const state = useImportedRunRestoration.getState().restoration;
  return state?.input === 'cad' && state.phase === 'ready' && state.isCurrent?.() !== false && state.ingestId === ingestId && state.submission
    ? structuredClone(state.submission) : null;
}
/** The revision an unedited restored CAD run is solved again with, or null. */
export function restoredCadRevision(ingestId?: string): string | null {
  return restoredCadSubmission(ingestId)
    ? useImportedRunRestoration.getState().restoration?.setupRevisionId ?? null : null;
}
export function restoredCadSetup(ingestId?: string): ImportedSetup | null {
  const submission = restoredCadSubmission(ingestId);
  if (!submission) return null;
  const { type: _type, ingest_id: _id, manifest_sha256: _manifest, artifact_sha256: _artifact,
    acknowledged_findings: _findings, ...geometry } = submission.geometry;
  return { geometry, options: submission.options };
}

// Only an explicit edit ends frozen CAD settings; background hydration does not.
subscribeSolveSettingsEdits(() => {
  const restoration = useImportedRunRestoration.getState().restoration;
  if (restoration?.phase === 'loading') {
    useImportedRunRestoration.setState({ restoration: { ...restoration, phase: 'failed',
      error: 'Settings changed while this run was loading. Select the run again before solving.' } });
    return;
  }
  const workspace = workspaceModeStore.getSnapshot();
  if (workspace.mode === 'cad' && workspace.importedInput === 'cad' && restoration?.input === 'cad' && restoration.submission) {
    useImportedRunRestoration.setState({ restoration: { ...restoration, submission: null } });
  }
});
