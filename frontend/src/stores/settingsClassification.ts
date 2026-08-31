/**
 * The stored-solve-settings half of the preference classification, plus the
 * coarse view of it that the durable namespaces get. See
 * `prefs/preferenceClassification.ts` for what the three effects mean and why
 * this is a source of truth rather than a wiring change.
 */
import type { PreferenceEffect } from '../prefs/preferenceClassification';
import type { SettingsNamespace } from './durableSettings';
import type { PersistedSolveOptions, PolarUiState } from './solveOptions';

/** Flat keys, with the directivity rig addressed one field at a time. */
export type SolveOptionKey = Exclude<keyof PersistedSolveOptions, 'polar'> | `polar.${keyof PolarUiState}`;

/**
 * Nearly all of these are written into the submitted `SolveOptions`, so nearly
 * all of them are solve-affecting -- including `diagonalAngle`, which only
 * picks which plane is sampled for display but decides what the solve measures
 * to draw it. The test this classification applies is not "does it ride along
 * in `polar_config`" but "does changing it make an earlier run no longer
 * comparable to the next one".
 *
 * `normAngle` is the one that fails that test. It still travels in
 * `polar_config`, and the server still shifts the stored patterns by it, but
 * the shift is a single constant per row and `withNormalizationAngle`
 * re-references any run to the value on screen at display time. Changing it
 * therefore redraws, and an archived run answers the new angle as readily as a
 * fresh one. See `results/normalization.ts` for why that composition is exact.
 */
export const SOLVE_OPTION_EFFECTS: Record<SolveOptionKey, PreferenceEffect> = {
  engine: 'solve-affecting',
  solverMode: 'solve-affecting',
  symmetry: 'solve-affecting',
  meshValidationMode: 'solve-affecting',
  verbose: 'solve-affecting',
  frequencySpacing: 'solve-affecting',
  frequencyMode: 'solve-affecting',
  frequencyListText: 'solve-affecting',
  'polar.angleStart': 'solve-affecting',
  'polar.angleEnd': 'solve-affecting',
  'polar.angleStep': 'solve-affecting',
  'polar.distance': 'solve-affecting',
  'polar.normAngle': 'render-refreshing',
  'polar.diagonalAngle': 'solve-affecting',
  'polar.enabledAxes': 'solve-affecting',
  'polar.observationOrigin': 'solve-affecting',
  'polar.sphericalSampling': 'solve-affecting',
  'polar.fieldPlane': 'solve-affecting',
};

export type UnclassifiedSolveOption = Exclude<keyof typeof SOLVE_OPTION_EFFECTS, SolveOptionKey>;
const _noStraySolveOptionKeys: UnclassifiedSolveOption[] = [];
void _noStraySolveOptionKeys;

/**
 * The same question asked of a whole durable namespace.
 *
 * A namespace holds many keys at once, so it is classified by the strongest
 * effect anything inside it can have: `preferences` is solve-affecting because
 * the run-naming settings live there, even though most of it only redraws.
 * Use the per-key records above wherever the individual key is known.
 */
export const SETTINGS_NAMESPACE_EFFECTS: Record<SettingsNamespace, PreferenceEffect> = {
  preferences: 'solve-affecting',
  solveOptions: 'solve-affecting',
  viewer: 'render-refreshing',
  theme: 'render-refreshing',
  // Panel sizes and which arrangement they were saved for.
  dockviewLayout: 'inert',
  dockviewMode: 'inert',
  workspaceMode: 'inert',
  cadProject: 'inert',
  // Which face of the crossover section is shown; the spec itself lives in
  // the return store and is unchanged by flipping the view.
  crossoverView: 'inert',
  crossoverGainUnit: 'inert',
  paramHelp: 'inert',
  paramSections: 'inert',
  cadSolveProfiles: 'inert',
  // Saved drivers are a catalogue, not a setting: nothing solves differently
  // until one is picked, and picking one is a rail edit of its own.
  driverLibrary: 'inert',
  // Not a preference at all: the autosaved design. It is in this record
  // because the namespace list has to be answered exhaustively, and a design
  // change is the most solve-affecting thing there is.
  designDraft: 'solve-affecting',
};

export type UnclassifiedNamespace = Exclude<keyof typeof SETTINGS_NAMESPACE_EFFECTS, SettingsNamespace>;
const _noStrayNamespaceKeys: UnclassifiedNamespace[] = [];
void _noStrayNamespaceKeys;
