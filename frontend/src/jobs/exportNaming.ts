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

/**
 * The design folder a run is archived under.
 *
 * Runs group by design rather than by pipeline: one design is edited
 * parametrically, sent to CAD and solved from the return, and splitting that
 * history across a `parametric/` and a `cadlink/` tree scatters the very thing
 * a person opens the folder to read. A CAD run reuses the stem its `.wglink`
 * bundle already owns, so a rename keeps writing to one folder.
 */
export function archiveFolderForJob(
  job: Pick<JobItem, 'label' | 'config_summary' | 'cad_source'>,
): string {
  const stem = job.cad_source?.archive_stem?.trim();
  // CAD archive stems are server-allocated ASCII folder keys whose uniqueness
  // is enforced case-insensitively. Treat that contract as opaque: slugging it
  // again here would give the browser a second, divergent filesystem rule.
  if (stem) return stem;
  return exportTitleSlug(job.label?.trim() || job.config_summary.formula_type || 'design');
}

/** Where a run's files land inside the workspace: `<design>/<run>`. */
export function exportSubdirectoryForJob(
  job: Pick<JobItem, 'run_number' | 'label' | 'config_summary' | 'cad_source'>,
): string {
  return `${archiveFolderForJob(job)}/${exportStemForJob(job)}`;
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
  job: Pick<JobItem, 'run_number' | 'label' | 'config_summary' | 'cad_source'>,
): string {
  return `${exportSubdirectoryForJob(job)}/${AUTO_EXPORT_FOLDER}`;
}

/** Which side of the app produced a run, for the run record. */
export function runSourceForJob(job: Pick<JobItem, 'config_summary'>): 'parametric' | 'cadlink' | 'mesh-file' {
  if (job.config_summary.import_origin === 'mesh-file') return 'mesh-file';
  return job.config_summary.geometry_type === 'imported' ? 'cadlink' : 'parametric';
}
