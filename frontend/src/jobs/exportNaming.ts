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
  const runNumber = job.run_number === null ? 'pending' : String(Math.max(1, Math.floor(job.run_number)));
  const title = job.label?.trim() || job.config_summary.formula_type || 'design';
  return `${runNumber}_${exportTitleSlug(title)}`;
}

/** The opaque location assigned by the server, including legacy paths. */
export function archiveFolderForJob(job: Pick<JobItem, 'archive_folder'>): string {
  if (!job.archive_folder) throw new Error('The server has not supplied this run’s archive folder');
  return job.archive_folder;
}

/** Logical run path. Writers also send job_id so the server chooses its recorded root. */
export function exportSubdirectoryForJob(job: Pick<JobItem, 'archive_folder' | 'archive_run'>): string {
  if (!job.archive_run) throw new Error('The server has not supplied this run’s archive name');
  return `${archiveFolderForJob(job)}/${job.archive_run}`;
}

/**
 * The folder, inside a run's own folder, that automatic export writes to.
 *
 * The run archive owns the names in the run folder itself -- `<run>.json`,
 * `<run>.csv`, `<run>_report.html` -- and builds them from the run's
 * completion time so a retry is byte identical. Automatic export builds some
 * of the same names from the current time. Sharing one folder made whichever
 * wrote second meet a file "with different content", which a background write
 * rightly refuses to overwrite: archive first blocked every export format,
 * export first blocked the archive. A folder of its own ends that for every
 * format, whatever the user selects.
 */
export const AUTO_EXPORT_FOLDER = 'exports';

/** Where automatic export writes a run's files: `<design>/<run>/exports`. */
export function autoExportSubdirectoryForJob(
  job: Pick<JobItem, 'archive_folder' | 'archive_run'>,
): string {
  return `${exportSubdirectoryForJob(job)}/${AUTO_EXPORT_FOLDER}`;
}

/** Which side of the app produced a run, for the run record. */
export function runSourceForJob(job: Pick<JobItem, 'config_summary'>): 'parametric' | 'cadlink' | 'mesh-file' {
  if (job.config_summary.import_origin === 'mesh-file') return 'mesh-file';
  return job.config_summary.geometry_type === 'imported' ? 'cadlink' : 'parametric';
}
