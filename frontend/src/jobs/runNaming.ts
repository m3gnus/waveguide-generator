/**
 * The label a solve is stored under.
 *
 * The run name used to be its own string -- a global `outputName` preference
 * that WG rewrote on every submission by incrementing its trailing digits.
 * That is why renaming a run never renamed the document: they were different
 * names, and only one of them moved.
 *
 * The name is now the document's `designName`, and this module only decorates
 * it. Nothing here writes the name back, so the sequence number and the date
 * are labels on a run rather than edits to the design.
 */
import { UNTITLED_DESIGN } from '../stores/designName';

export type RunNameDatePosition = 'off' | 'prefix' | 'suffix';
export type RunNameDateFormat = 'yymmdd' | 'yyyy-mm-dd';
export type RunNameNumberPosition = 'off' | 'suffix';
export type RunNameNumberFormat = 'natural' | '2-digit' | '3-digit';

export interface RunNameOptions {
  runNameDatePosition: RunNameDatePosition;
  runNameDateFormat: RunNameDateFormat;
  runNameNumberPosition: RunNameNumberPosition;
  runNameNumberFormat: RunNameNumberFormat;
}

/**
 * Which design the run counter belongs to, and the number the next run takes.
 *
 * Two fields rather than a map keyed by name: the counter only ever describes
 * the design on screen, and a map would grow a row for every name ever typed.
 * Renaming the design starts its numbering again at 1, which is what makes
 * "Tritonia-M01" mean the first run of Tritonia-M.
 */
export interface RunSequenceState {
  runSequenceName: string;
  runSequenceNext: number;
}

export function normalizeRunName(value: unknown, fallback = UNTITLED_DESIGN): string {
  return String(value ?? '').trim() || fallback;
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

/** The number the next run of this design takes. */
export function runSequenceFor(state: RunSequenceState, designName: string): number {
  if (normalizeRunName(state.runSequenceName) !== normalizeRunName(designName)) return 1;
  return Math.max(1, Math.floor(state.runSequenceNext) || 1);
}

/** The counter state to store once a submission under this name succeeded. */
export function advanceRunSequence(state: RunSequenceState, designName: string): RunSequenceState {
  return {
    runSequenceName: normalizeRunName(designName),
    runSequenceNext: runSequenceFor(state, designName) + 1,
  };
}

/**
 * Decorate a design name into a run label.
 *
 * The core is never modified: it is the document's name, and a run is an event
 * in that document's life rather than a new document.
 */
export function decorateRunName(
  designName: string,
  options: RunNameOptions,
  sequence = 1,
  now = new Date(),
): string {
  const core = normalizeRunName(designName);
  const numbered = options.runNameNumberPosition === 'off'
    ? core
    : `${core}${String(Math.max(1, Math.floor(sequence) || 1)).padStart(runNumberWidth(options.runNameNumberFormat), '0')}`;
  if (options.runNameDatePosition === 'off') return numbered;
  const date = runNameDateFor(now, options.runNameDateFormat);
  return options.runNameDatePosition === 'prefix' ? `${date}_${numbered}` : `${numbered}_${date}`;
}

/** The label the next submission of this design would receive. */
export function nextRunLabel(
  designName: string,
  options: RunNameOptions & RunSequenceState,
  now = new Date(),
): string {
  return decorateRunName(designName, options, runSequenceFor(options, designName), now);
}
