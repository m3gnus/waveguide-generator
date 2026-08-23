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
 * Every one of these is written into the submitted `SolveOptions`, so all of
 * them are solve-affecting -- including the two that only move what is drawn.
 * `normAngle` shifts the plotted curves and `diagonalAngle` picks which plane
 * is sampled for display, but both travel in `polar_config`, so changing
 * either does change the next submission and does make an earlier run no
 * longer comparable to it. That is the test this classification applies.
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
  'polar.normAngle': 'solve-affecting',
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
  paramHelp: 'inert',
  paramSections: 'inert',
  cadSolveProfiles: 'inert',
  cadAcknowledgedFindings: 'inert',
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
