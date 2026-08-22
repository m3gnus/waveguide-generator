import type { JobItem } from './jobsSocket';
import type { CadLinkedDesignSummary } from './cadlink';

/**
 * One captured CAD document: the model state a set of runs was solved from.
 *
 * Deliberately not called a "version". Nothing here was versioned by a person —
 * this is the geometry as it stood when a return arrived, addressed by the hash
 * of that state. Runs sharing a hash were solved from one identical model.
 */
export interface CadProjectDocument {
  returnStateHash: string;
  documentName: string | null;
  ingestId: string | null;
  returnId: string | null;
  capturedAt: string | null;
  /** Absent when the sidecar survived but the document itself did not. */
  filename: string | null;
  bytes: number | null;
}

export interface CadProjectDocumentListing {
  archiveStem: string;
  folder: string;
  items: CadProjectDocument[];
}

export interface CadProject extends CadLinkedDesignSummary {
  /** The archive folder this project's runs and CAD documents share. */
  archiveStem: string | null;
}

interface CadProjectListing {
  /** The server returns one canonical, newest design head per lineage. */
  items: CadProject[];
}

async function failure(response: Response): Promise<Error> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return new Error(body.detail);
  } catch { /* the status is still worth reporting */ }
  return new Error(`CAD project request failed (${response.status})`);
}

export async function listCadProjects(fetcher: typeof fetch = fetch): Promise<CadProject[]> {
  const response = await fetcher('/api/cadlink/designs');
  if (!response.ok) throw await failure(response);
  return ((await response.json()) as CadProjectListing).items;
}

export async function listProjectDocuments(
  lineageId: string,
  fetcher: typeof fetch = fetch,
): Promise<CadProjectDocumentListing> {
  const response = await fetcher(`/api/cadlink/projects/${encodeURIComponent(lineageId)}/documents`);
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<CadProjectDocumentListing>;
}

/** Where the browser should send someone to download one version's model. */
export function projectDocumentUrl(lineageId: string, returnStateHash: string): string {
  return `/api/cadlink/projects/${encodeURIComponent(lineageId)}/documents/${encodeURIComponent(returnStateHash)}`;
}

export async function revealProjectFolder(
  lineageId: string,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  const response = await fetcher(`/api/cadlink/projects/${encodeURIComponent(lineageId)}/reveal`, { method: 'POST' });
  if (!response.ok) throw await failure(response);
  return ((await response.json()) as { path: string }).path;
}

/**
 * Ask the server to file this run's CAD document beside the run.
 *
 * The run-folder name is passed rather than derived: the caller has just used
 * that name to write the folder, and a second implementation of the naming rule
 * on the server could only ever agree by coincidence.
 */
export async function archiveRunCadDocument(
  request: { subdirectory: string; runStem: string; archiveStem: string; returnStateHash: string },
  fetcher: typeof fetch = fetch,
): Promise<{ placed: boolean; relativePath?: string | null; reason?: string }> {
  const response = await fetcher('/api/cadlink/runs/archive-document', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<{ placed: boolean; relativePath?: string | null; reason?: string }>;
}

/** The CAD model state a run was solved from, or null for a run without one. */
export function runReturnStateHash(job: Pick<JobItem, 'cad_source'>): string | null {
  return job.cad_source?.return_state_hash ?? null;
}

/**
 * Which project a run belongs to.
 *
 * The lineage is the identity; the archive stem is only what the folder is
 * called. A run that carries neither belongs to no project and stays out of the
 * project history rather than being guessed into someone else's.
 */
export function runProjectLineage(job: Pick<JobItem, 'cad_source' | 'config_summary'>): string | null {
  if (job.config_summary?.geometry_type !== 'imported') return null;
  return job.cad_source?.lineage_id ?? null;
}

/** This project's runs, newest first. */
export function runsForProject(jobs: JobItem[], lineageId: string | null): JobItem[] {
  if (!lineageId) return [];
  return jobs.filter((job) => runProjectLineage(job) === lineageId);
}

export interface RunGroup {
  returnStateHash: string | null;
  document: CadProjectDocument | null;
  runs: JobItem[];
}

/**
 * Split a project's runs where the CAD geometry changed.
 *
 * The list stays flat and chronological — one run per row, as the jobs rail
 * shows them — and a boundary appears only where consecutive runs came from
 * different model states. That boundary is the thing a person recognises
 * ("this is where I changed the flange"), and it is where the model for that
 * stretch of runs is offered for download. Nothing is numbered: these groups
 * are discovered from what still exists, so an ordinal would silently change
 * meaning as runs age out.
 */
export function groupRunsByModelState(
  runs: JobItem[],
  documents: CadProjectDocument[],
): RunGroup[] {
  const byHash = new Map(documents.map((document) => [document.returnStateHash, document]));
  const groups: RunGroup[] = [];
  runs.forEach((run) => {
    const hash = runReturnStateHash(run);
    const last = groups.at(-1);
    if (last && last.returnStateHash === hash) {
      last.runs.push(run);
      return;
    }
    groups.push({ returnStateHash: hash, document: hash ? byHash.get(hash) ?? null : null, runs: [run] });
  });
  return groups;
}

/**
 * File a completed run's CAD document beside the run, when the setting asks.
 *
 * Advisory by construction: the run archive is already written by the time this
 * runs, and a missing convenience copy must never make a good run look failed.
 */
export async function placeRunCadDocument(
  job: JobItem,
  subdirectory: string,
  runStem: string,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const archiveStem = job.cad_source?.archive_stem;
  const returnStateHash = runReturnStateHash(job);
  if (!archiveStem || !returnStateHash) return;
  await archiveRunCadDocument({ subdirectory, runStem, archiveStem, returnStateHash }, fetcher);
}
