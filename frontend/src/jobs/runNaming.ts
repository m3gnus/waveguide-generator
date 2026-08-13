import { submittedProjectionsEqual, type SubmittedDesignProjection } from './submittedProjection';

export const UNBOUND_RUN_NAME_SOURCE = 'unbound' as const;
export type RunNameSourceProjection = SubmittedDesignProjection | typeof UNBOUND_RUN_NAME_SOURCE | null;

export interface RunNamingState {
  outputName: string;
  nameSourceProjection: RunNameSourceProjection;
  runNameNumberPosition?: RunNameNumberPosition;
  runNameNumberFormat?: RunNameNumberFormat;
}

export type RunNameDatePosition = 'off' | 'prefix' | 'suffix';
export type RunNameDateFormat = 'yymmdd' | 'yyyy-mm-dd';
export type RunNameNumberPosition = 'off' | 'suffix';
export type RunNameNumberFormat = 'natural' | '2-digit' | '3-digit';

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

/** Increment only the final digit run, retaining or applying its minimum width. */
export function incrementTrailingDigits(name: string, minimumWidth = 1): string {
  const normalized = normalizeRunName(name);
  const match = /^(.*?)(\d+)$/.exec(normalized);
  const width = Math.max(1, Math.floor(minimumWidth));
  if (!match) return `${normalized}${String(2).padStart(width, '0')}`;
  const incremented = (BigInt(match[2]) + 1n).toString().padStart(Math.max(match[2].length, width), '0');
  return `${match[1]}${incremented}`;
}

function runNumberWidth(format: RunNameNumberFormat | undefined): number {
  if (format === '2-digit') return 2;
  if (format === '3-digit') return 3;
  return 1;
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
  const current = normalizeRunName(naming.outputName);
  // Null means no name has been chosen yet and retains the filename default.
  // Unbound means the user committed this name while no valid solve projection
  // existed; the first projection adopts that name instead of replacing it.
  if (naming.nameSourceProjection === UNBOUND_RUN_NAME_SOURCE) return current;
  if (naming.nameSourceProjection === null) return runNameFromFilename(documentFilename);
  if (submittedProjectionsEqual(naming.nameSourceProjection, projection)) return current;
  if (naming.runNameNumberPosition === 'off') return current;
  return incrementTrailingDigits(current, runNumberWidth(naming.runNameNumberFormat));
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
