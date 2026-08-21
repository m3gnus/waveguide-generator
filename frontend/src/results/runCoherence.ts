import { useMemo, useSyncExternalStore } from 'react';
import type { JobItem } from '../api/jobsSocket';
import { hydrateJobDesign } from '../jobs/jobDesign';
import { useCadReturnStore } from '../stores/cadReturn';
import { serializeDesign, useDesignStore, type DesignDocument } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';

export interface RunContext {
  mode: WorkspaceMode;
  designRevision: number;
  /** Content identity of the live design; see `designFingerprint`. */
  designFingerprint: string;
  ingestId: string | null;
  designId: string | null;
}

export type RunContextVerdict = 'current' | 'older-revision' | 'other-model';

/** What a mismatched run's marker says, and what its tooltip spells out. */
export const RUN_VERDICT_MARKER: Record<Exclude<RunContextVerdict, 'current'>, string> = {
  'older-revision': 'edited since',
  'other-model': 'other model',
};
export const RUN_VERDICT_SENTENCE: Record<Exclude<RunContextVerdict, 'current'>, string> = {
  'older-revision': 'The design has been edited since this run was solved.',
  'other-model': "This run's model is not the one in the viewport.",
};

/**
 * A content identity for a design: equal fingerprints mean the same model.
 *
 * Taken from the serialized wire form rather than from the store document,
 * because that is the form a run was solved from and the form its snapshot is
 * read back as -- a fingerprint is only worth anything if a design reopened
 * from a file, undone back to what was solved, or browsed to in the run list
 * hashes equal to the run it came from. Everything `serializeDesign` drops is
 * therefore excluded by construction: `simulation.solver_mode` (a machine
 * preference that never leaves this computer), `enclosure.baffle_margin` and
 * the `quadrants` mirror, and `guiding_curve` off OSSE. `_expressions` is
 * stripped on top of that, being ATH spelling for a value already present as a
 * scalar; `_absent` is kept, because it decides whether the wire states a
 * value or leaves it to the server.
 */
const designFingerprints = new WeakMap<DesignDocument, string>();

export function designFingerprint(design: DesignDocument): string {
  // Keyed by document identity: the store hands out a new object per edit, so
  // this is one hash per edit however many surfaces ask for it per render.
  const cached = designFingerprints.get(design);
  if (cached !== undefined) return cached;
  const { _expressions, ...rest } = design;
  const fingerprint = fnv1a(canonicalJson(serializeDesign(rest as DesignDocument)));
  designFingerprints.set(design, fingerprint);
  return fingerprint;
}

/** Key order is an artifact of how a document was built, never a difference. */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/** FNV-1a, widened by the canonical length so the compare keys stay short. */
function fnv1a(text: string): string {
  let value = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    value ^= text.charCodeAt(index);
    value = Math.imul(value, 0x01000193);
  }
  return `${(value >>> 0).toString(36)}.${text.length.toString(36)}`;
}

/**
 * The fingerprint of the design a run was solved from, or null when the run
 * stored none this build can read.
 *
 * Cached against the snapshot the job arrived with: a run's stored design is
 * immutable, so the hash survives every jobs message that re-sends the row.
 */
const jobDesignFingerprints = new Map<string, { snapshot: JobItem['script_snapshot']; fingerprint: string | null }>();

export function jobDesignFingerprint(job: Pick<JobItem, 'id' | 'script_snapshot'>): string | null {
  const cached = jobDesignFingerprints.get(job.id);
  if (cached && cached.snapshot === job.script_snapshot) return cached.fingerprint;
  const design = hydrateJobDesign(job);
  const fingerprint = design ? designFingerprint(design) : null;
  jobDesignFingerprints.set(job.id, { snapshot: job.script_snapshot, fingerprint });
  return fingerprint;
}

/** The model identity represented by the workspace and viewport right now. */
export function runContext(): RunContext {
  const design = useDesignStore.getState();
  return {
    mode: workspaceModeStore.getSnapshot().mode,
    designRevision: design.designRevision,
    designFingerprint: designFingerprint(design.design),
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
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  const ingestId = useCadReturnStore((state) => state.ingestRecord?.ingest_id ?? null);
  const designId = useDocumentStore((state) => state.identity?.designId ?? null);
  return { mode, designRevision, designFingerprint: useMemo(() => designFingerprint(design), [design]), ingestId, designId };
}

/**
 * Whether a saved run describes the model currently represented by the editor.
 *
 * Parametric identity is the design's content, not the revision counter it was
 * submitted with: that counter moves on every edit, every undo and every file
 * open, so it could only ever call the newest solve current -- including after
 * an edit that was undone. Imported CAD identity is the immutable ingestion
 * whose mesh occupies the CAD viewport. A run whose stored design cannot be
 * read back is not the design on screen as far as anything here can tell.
 */
export function runMatchesContext(job: JobItem, context: RunContext): RunContextVerdict {
  const imported = job.config_summary?.geometry_type === 'imported';
  if (!imported && context.mode === 'parametric') {
    const fingerprint = jobDesignFingerprint(job);
    return fingerprint !== null && fingerprint === context.designFingerprint ? 'current' : 'older-revision';
  }
  if (imported && context.mode === 'cad') {
    return context.ingestId !== null && job.cad_source?.ingest_id === context.ingestId ? 'current' : 'other-model';
  }
  return 'other-model';
}

/**
 * Where a run came from, stated only when it differs from where the workspace
 * is: in CAD mode every run in the dock is a CAD run, so a `CAD` pill on each
 * of them says nothing. The marker is the exception, never the rule.
 */
export function runProvenanceMarker(job: Pick<JobItem, 'config_summary'>, mode: WorkspaceMode): string | null {
  if (job.config_summary?.geometry_type === 'imported') return mode === 'cad' ? null : 'CAD';
  return mode === 'cad' ? 'Parametric' : null;
}

/** Compact provenance marker used wherever a run can join a comparison. */
export function runContextMarker(job: JobItem, context: RunContext): string | null {
  const provenance = runProvenanceMarker(job, context.mode);
  if (provenance || job.config_summary?.geometry_type === 'imported') return provenance;
  return runMatchesContext(job, context) === 'current' ? null : RUN_VERDICT_MARKER['older-revision'];
}
