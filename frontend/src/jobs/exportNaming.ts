import type { JobItem } from '../api/jobsSocket';
import { designNameSlug } from '../stores/designName';

/**
 * Convert a human job title to the portable slug used only in export paths.
 *
 * One slug rule, shared with the `.cfg` filename: the two used to be separate
 * implementations that could disagree about the same typed name.
 */
export function exportTitleSlug(value: unknown): string {
  return designNameSlug(value, 'design');
}

/** Stable stem shared by every export and mesh download for a stored job. */
export function exportStemForJob(
  job: Pick<JobItem, 'run_number' | 'label' | 'config_summary'>,
): string {
  const runNumber = Math.max(1, Math.floor(job.run_number));
  const title = job.label?.trim() || job.config_summary.formula_type || 'design';
  return `${runNumber}_${exportTitleSlug(title)}`;
}
