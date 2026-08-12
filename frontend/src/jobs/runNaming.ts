import { submittedProjectionsEqual, type SubmittedDesignProjection } from './submittedProjection';

export interface RunNamingState {
  outputName: string;
  nameSourceProjection: SubmittedDesignProjection | null;
}

export type RunNameDatePosition = 'off' | 'prefix' | 'suffix';
export type RunNameDateFormat = 'yymmdd' | 'yyyy-mm-dd';

export interface RunNameDateOptions {
  runNameDatePosition: RunNameDatePosition;
  runNameDateFormat: RunNameDateFormat;
}

export function normalizeRunName(value: unknown, fallback = 'horn'): string {
  return String(value ?? '').trim() || fallback;
}

/** Extract a human run name without applying filesystem slug rules. */
export function runNameFromFilename(filename: string): string {
  const basename = String(filename).replace(/^.*[\\/]/, '');
  return normalizeRunName(basename.replace(/\.(cfg|txt|mwg)$/i, ''));
}

/** Increment only the final digit run, retaining its width while possible. */
export function incrementTrailingDigits(name: string): string {
  const normalized = normalizeRunName(name);
  const match = /^(.*?)(\d+)$/.exec(normalized);
  if (!match) return `${normalized}2`;
  const incremented = (BigInt(match[2]) + 1n).toString().padStart(match[2].length, '0');
  return `${match[1]}${incremented}`;
}

/** Format a local calendar date without letting locale settings alter labels. */
export function runNameDateFor(
  now = new Date(),
  format: RunNameDateFormat = 'yymmdd',
): string {
  const year = format === 'yymmdd'
    ? String(now.getFullYear() % 100).padStart(2, '0')
    : String(now.getFullYear()).padStart(4, '0');
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return format === 'yymmdd' ? `${year}${month}${day}` : `${year}-${month}-${day}`;
}

/** Decorate a computed core name only at the user-facing label boundary. */
export function decorateRunName(
  coreName: string,
  options: RunNameDateOptions,
  now = new Date(),
): string {
  const core = normalizeRunName(coreName);
  if (options.runNameDatePosition === 'off') return core;
  const date = runNameDateFor(now, options.runNameDateFormat);
  return options.runNameDatePosition === 'prefix' ? `${date}_${core}` : `${core}_${date}`;
}

/** The label a submission would receive without mutating its persisted state. */
export function nextRunName(
  naming: RunNamingState,
  projection: SubmittedDesignProjection,
  documentFilename = '',
): string {
  if (!naming.nameSourceProjection) return runNameFromFilename(documentFilename);
  const current = normalizeRunName(naming.outputName);
  return submittedProjectionsEqual(naming.nameSourceProjection, projection)
    ? current
    : incrementTrailingDigits(current);
}

/** The decorated label for a submission; nextRunName itself remains core-only. */
export function nextRunLabel(
  naming: RunNamingState & RunNameDateOptions,
  projection: SubmittedDesignProjection,
  documentFilename = '',
  now = new Date(),
): string {
  return decorateRunName(nextRunName(naming, projection, documentFilename), naming, now);
}
