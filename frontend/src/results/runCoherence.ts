import { useSyncExternalStore } from 'react';
import type { JobItem } from '../api/jobsSocket';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';

export interface RunContext {
  mode: WorkspaceMode;
  designRevision: number;
  ingestId: string | null;
  designId: string | null;
}

export type RunContextVerdict = 'current' | 'older-revision' | 'other-model';

/** The model identity represented by the workspace and viewport right now. */
export function runContext(): RunContext {
  return {
    mode: workspaceModeStore.getSnapshot().mode,
    designRevision: useDesignStore.getState().designRevision,
    ingestId: useCadReturnStore.getState().ingestRecord?.ingest_id ?? null,
    designId: useDocumentStore.getState().identity?.designId ?? null,
  };
}

/** Reactive form of runContext for surfaces that need to update their verdicts. */
export function useRunContext(): RunContext {
  const mode = useSyncExternalStore(
    workspaceModeStore.subscribe,
    workspaceModeStore.getSnapshot,
    workspaceModeStore.getSnapshot,
  ).mode;
  const designRevision = useDesignStore((state) => state.designRevision);
  const ingestId = useCadReturnStore((state) => state.ingestRecord?.ingest_id ?? null);
  const designId = useDocumentStore((state) => state.identity?.designId ?? null);
  return { mode, designRevision, ingestId, designId };
}

/**
 * Whether a saved run describes the model currently represented by the editor.
 *
 * Parametric identity is the revision submitted with the solve. Imported CAD
 * identity is the immutable ingestion whose mesh occupies the CAD viewport.
 */
export function runMatchesContext(job: JobItem, context: RunContext): RunContextVerdict {
  const imported = job.config_summary?.geometry_type === 'imported';
  if (!imported && context.mode === 'parametric') {
    return job.design_revision === context.designRevision ? 'current' : 'older-revision';
  }
  if (imported && context.mode === 'cad') {
    return context.ingestId !== null && job.cad_source?.ingest_id === context.ingestId ? 'current' : 'other-model';
  }
  return 'other-model';
}

/** Compact provenance marker used wherever a run can join a comparison. */
export function runContextMarker(job: JobItem, context: RunContext): string | null {
  if (job.config_summary?.geometry_type === 'imported') return 'CAD import';
  return job.design_revision !== context.designRevision ? `rev ${job.design_revision}` : null;
}
