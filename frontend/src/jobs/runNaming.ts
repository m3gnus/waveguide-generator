import { submittedProjectionsEqual, type SubmittedDesignProjection } from './submittedProjection';

export interface RunNamingState {
  outputName: string;
  nameSourceProjection: SubmittedDesignProjection | null;
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
