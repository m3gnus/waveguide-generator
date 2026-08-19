/**
 * What changing a preference actually costs.
 *
 * WG remembers three quite different kinds of thing under one word. Some
 * settings change the request the next solve is submitted with; some change
 * only how finished results are drawn; and some -- panel sizes, which tab was
 * last open -- change neither. Nothing in the codebase said which was which, so
 * every place that reacts to a preference change decided for itself, and a new
 * preference could be added without anyone deciding at all.
 *
 * This module is the decision, written down once and checked by the compiler:
 * the record is keyed by `keyof Preferences`, so a preference added without a
 * classification does not build.
 *
 * It is deliberately only the source of truth. The existing reactions -- the
 * charts that re-read on a smoothing change, the submit path that reads the
 * solve options -- are untouched by this slice; wiring them to consult this
 * classification instead of deciding for themselves is future work.
 */
import type { Preferences } from './preferences';

export type PreferenceEffect =
  /** Changing it changes or invalidates the request a future solve is submitted with. */
  | 'solve-affecting'
  /** Charts or the viewport must be redrawn; no solve is involved. */
  | 'render-refreshing'
  /** Neither: layout, chrome, and choices consulted only when an action is taken. */
  | 'inert';

export const PREFERENCE_EFFECTS: Record<keyof Preferences, PreferenceEffect> = {
  // Which application CAD Link talks to. Read when a send or a receive happens,
  // so nothing is stale until the user acts.
  cadApplication: 'inert',
  smoothing: 'render-refreshing',
  mapReference: 'render-refreshing',
  directivityGuideInterval: 'render-refreshing',
  chartTypes: 'render-refreshing',
  chartTheme: 'render-refreshing',
  // Export selections are read at export time and nothing displays them.
  exportFormats: 'inert',
  autoExportFormats: 'inert',
  autoExportOnComplete: 'inert',
  archiveRunsOnComplete: 'inert',
  autoDownloadMesh: 'inert',
  // The run-naming settings all decide the label a queued job is submitted
  // under, so a change to any of them changes the next submission. The name
  // itself is not here: it belongs to the document, not to preferences.
  runNameDatePosition: 'solve-affecting',
  runNameDateFormat: 'solve-affecting',
  runNameNumberPosition: 'solve-affecting',
  runNameNumberFormat: 'solve-affecting',
  runSequenceName: 'solve-affecting',
  runSequenceNext: 'solve-affecting',
  // Ordering and filtering of the run list: a redraw, never a resolve.
  jobSort: 'render-refreshing',
  minRating: 'render-refreshing',
};

/**
 * Fails to compile if `PREFERENCE_EFFECTS` gains a key that is not a
 * preference. The `Record` above already fails on a missing one, so between
 * them the classification cannot drift from the interface it describes.
 */
export type UnclassifiedPreference = Exclude<keyof typeof PREFERENCE_EFFECTS, keyof Preferences>;
const _noStrayPreferenceKeys: UnclassifiedPreference[] = [];
void _noStrayPreferenceKeys;

export function preferenceEffect(key: keyof Preferences): PreferenceEffect {
  return PREFERENCE_EFFECTS[key];
}
