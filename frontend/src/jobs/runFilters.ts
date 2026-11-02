import { create } from 'zustand';
import type { JobItem } from '../api/jobsSocket';
import { isFailedCadPreparation, isHeldCadJob } from './cadSolve';
import { applyJobPreferences, runDisplayName, type JobSort } from '../prefs/preferences';
import { jobDesignFingerprint } from '../results/runCoherence';
import { runOrigin, runSourceText, type RunOrigin } from './runIdentity';

export interface RunFilters {
  query: string; source: 'all' | RunOrigin;
  project: string; model: string;
}

export const useRunFilters = create<RunFilters>(() => ({ query: '', source: 'all', project: '', model: '' }));

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

/** The runs in the rail before filters. A held request and a failed preparation
 * never became runs; CAD Link and Results present them. */
export function railRuns(jobs: JobItem[]): JobItem[] {
  return jobs.filter(job => !isHeldCadJob(job) && !isFailedCadPreparation(job)
    && !(job.cad_intent && job.cad_state?.reason === 'superseded'));
}

/** The rail's list as it stands: its sort, its minimum rating and its filters. */
export function listedRuns(runs: JobItem[], { query, source, project, model }: RunFilters, sort: JobSort, minimumRating: number): JobItem[] {
  const wanted = query.trim().toLocaleLowerCase();
  const scoped = applyJobPreferences(runs, sort, minimumRating).filter(job => (source === 'all' || runOrigin(job) === source)
    && (!project || job.cad_source?.lineage_id === project) && (!model || runModelKey(job) === model));
  if (!wanted) return scoped;
  const numberQuery = wanted.replace(/^#/, '');
  return scoped.filter((job) => {
    const formula = String(job.config_summary.formula_type ?? '').toLocaleLowerCase();
    const title = runDisplayName(job).toLocaleLowerCase();
    const runNumberMatches = /^#?\d+$/.test(wanted) && job.run_number !== null && String(job.run_number).includes(numberQuery);
    return title.includes(wanted) || runNumberMatches || formula.includes(wanted) || runSourceText(job).toLocaleLowerCase().includes(wanted);
  });
}

export function showProjectRuns(lineageId: string): void {
  useRunFilters.setState({ project: lineageId, model: '', source: 'all', query: '' });
}
