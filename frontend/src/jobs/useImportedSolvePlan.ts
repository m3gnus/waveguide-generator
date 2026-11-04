import { showsRetainedMesh, type CurrentModel } from '../model/currentModel';
import { designAssemblySnapshot, NEEDS_BUILD } from './assemblySolve';
import { useDesignStore } from '../stores/design';
import { useMemo, useSyncExternalStore } from 'react';
import {
  importedSolvePlanRequestBody,
  postImportedSolvePlan,
  type ImportedSolvePlan,
  type ImportedSolveSubmission,
} from './actions';
import { buildImportedSubmission, type PreparedSubmission } from './importedSubmission';
import { capabilityFingerprint } from './capabilityFingerprint';
import { useCapabilities } from './useCapabilities';
import { usePlanQuery } from './usePlanQuery';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { workspaceModeStore } from '../stores/workspaceMode';
import { fileMeshSubmission, fileSubmissionSnapshot, useMeshImportStore } from '../stores/meshImport';
import { useCadReturnStore } from '../stores/cadReturn';
import { useImportedSolveOptionsStore } from '../stores/solveOptions';
import { useImportedModelStore } from '../stores/importedModel';
import { failureWords } from '../api/httpError';

/** Producer dispatch for every ready-mesh consumer. An unbuilt live design can prepare on press. */
export function activeReadyMeshSubmission(model: Pick<CurrentModel, 'kind'>): PreparedSubmission | 'needs-build' | Error | null {
  try {
    if (showsRetainedMesh(model.kind)) return fileSubmissionSnapshot();
    if (model.kind === 'parametric' && useDesignStore.getState().design.assembly) return designAssemblySnapshot();
    return null;
  } catch (error) {
    if (error instanceof Error && error.message === NEEDS_BUILD) return 'needs-build';
    return error instanceof Error ? error : new Error(failureWords(error));
  }
}

export interface ImportedSolvePlanSnapshot {
  plan: ImportedSolvePlan | null;
  error: string | null;
  isPending: boolean;
}

/**
 * The server's verdict, per engine, on the CAD return the user would solve now.
 *
 * Imported geometry takes the same engine choice as a parametric design, and
 * which engines can take this particular return -- its domain, its features,
 * its frame -- is the server's call (`POST /api/solve/imported-plan`). The
 * solver selector, the Solve control, the status bar and the CAD Link notice
 * all read this one answer rather than each re-deriving it here.
 *
 * The request is exactly the submission `buildImportedSubmission` would send,
 * so the plan is keyed by the ingested record and by the user's current
 * choices. The capability snapshot is part of the key as well: an engine
 * becoming available -- BEAT's CPU runtime finishing its preparation -- changes
 * the answer without anything the user did.
 */
export function useImportedSolvePlan(enabled: boolean, preparedSubmission?: ImportedSolveSubmission | null): ImportedSolvePlanSnapshot {
  const opened = useImportedModelStore();
  const workspace = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot);
  const file = useMeshImportStore();
  const viewport = useSyncExternalStore(importedMeshStore.subscribe, importedMeshStore.getSnapshot);
  const cadReturn = useCadReturnStore();
  const solveOptions = useImportedSolveOptionsStore();
  const { engines } = useCapabilities();
  const currentBody = useMemo(() => {
    if (!enabled) return null;
    if (preparedSubmission !== undefined) return preparedSubmission === null ? null : importedSolvePlanRequestBody(preparedSubmission);
    if (workspace.importedInput === 'file') {
      try { return importedSolvePlanRequestBody(fileMeshSubmission()); } catch { return null; }
    }
    if (!cadReturn.ingestRecord) return null;
    try {
      return importedSolvePlanRequestBody(buildImportedSubmission(cadReturn));
    } catch {
      // A submission that cannot be built has its own blocker on screen; there
      // is nothing to plan until it is fixed.
      return null;
    }
    // `solveOptions` is read inside buildImportedSubmission, from the store.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, cadReturn, solveOptions, preparedSubmission, workspace.importedInput, file, viewport, opened]);
  const capabilityKey = capabilityFingerprint(engines);
  const query = usePlanQuery(currentBody, 'imported-solve-plan', postImportedSolvePlan, [capabilityKey]);
  return {
    ...query,
    error: query.error ? failureWords(query.error) : null,
  };
}
