import { create } from 'zustand';
import type { JobItem } from '../api/jobsSocket';
import { jobDesignFingerprint } from '../results/runCoherence';
import { runOrigin, type RunOrigin } from './runIdentity';

export const useRunFilters = create<{
  query: string; source: 'all' | RunOrigin;
  project: string; model: string;
}>(() => ({ query: '', source: 'all', project: '', model: '' }));

/** A missing state hash never asserts that unrelated ingestions share a model. */
export function runModelKey(job: JobItem): string {
  const source = runOrigin(job);
  if (source === 'parametric') {
    let fingerprint: string | null = null;
    try { fingerprint = jobDesignFingerprint(job); }
    catch { /* Old or incomplete snapshots remain individual, readable runs. */ }
    return `design:${fingerprint ?? job.id}`;
  }
  const cad = job.cad_source;
  return `${source}:${cad?.lineage_id ?? ''}:${cad?.return_state_hash ?? cad?.ingest_id ?? job.id}`;
}

export function showProjectRuns(lineageId: string): void {
  useRunFilters.setState({ project: lineageId, model: '', source: 'all', query: '' });
}
