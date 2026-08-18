import { describe, expect, it } from 'vitest';
import { SETTINGS_NAMESPACES } from '../stores/durableSettings';
import { SETTINGS_NAMESPACE_EFFECTS, SOLVE_OPTION_EFFECTS } from '../stores/settingsClassification';
import { defaultPolarUi, defaultSolveOptions } from '../stores/solveOptions';
import { DEFAULT_VIEWER_PREFERENCES } from '../viewerprefs/viewerPreferences';
import { VIEWER_PREFERENCE_EFFECTS } from '../viewerprefs/viewerPreferenceClassification';
import { PREFERENCE_EFFECTS, preferenceEffect, type PreferenceEffect } from './preferenceClassification';
import { normalize } from './preferences';

const EFFECTS: PreferenceEffect[] = ['solve-affecting', 'render-refreshing', 'inert'];

/**
 * The compiler already refuses a classification that has drifted from the
 * interface it describes. These check the same thing against a value that is
 * built at runtime rather than declared, so a key that exists only in a stored
 * payload -- or one dropped from an interface but left in the record -- is
 * caught as well.
 */
describe('every preference is classified', () => {
  it('covers exactly the keys a normalized preference payload carries', () => {
    expect(Object.keys(PREFERENCE_EFFECTS).sort()).toEqual(Object.keys(normalize({})).sort());
  });

  it('covers exactly the viewer preferences', () => {
    expect(Object.keys(VIEWER_PREFERENCE_EFFECTS).sort()).toEqual(Object.keys(DEFAULT_VIEWER_PREFERENCES).sort());
  });

  it('covers exactly the persisted solve options, rig fields included', () => {
    const { polar, ...flat } = defaultSolveOptions();
    expect(Object.keys(SOLVE_OPTION_EFFECTS).sort()).toEqual([
      ...Object.keys(flat),
      ...Object.keys(polar).map((key) => `polar.${key}`),
    ].sort());
    expect(Object.keys(polar).sort()).toEqual(Object.keys(defaultPolarUi).sort());
  });

  it('covers exactly the durable namespaces', () => {
    expect(Object.keys(SETTINGS_NAMESPACE_EFFECTS).sort()).toEqual(Object.keys(SETTINGS_NAMESPACES).sort());
  });

  it('uses only the three declared effects', () => {
    const every = [
      ...Object.values(PREFERENCE_EFFECTS),
      ...Object.values(VIEWER_PREFERENCE_EFFECTS),
      ...Object.values(SOLVE_OPTION_EFFECTS),
      ...Object.values(SETTINGS_NAMESPACE_EFFECTS),
    ];
    expect(every.filter((effect) => !EFFECTS.includes(effect))).toEqual([]);
  });
});

describe('the classification says what it is meant to say', () => {
  it('treats display settings as a redraw', () => {
    expect(preferenceEffect('smoothing')).toBe('render-refreshing');
    expect(preferenceEffect('chartTypes')).toBe('render-refreshing');
    expect(preferenceEffect('directivityGuideInterval')).toBe('render-refreshing');
    expect(preferenceEffect('mapReference')).toBe('render-refreshing');
    expect(preferenceEffect('jobSort')).toBe('render-refreshing');
    expect(VIEWER_PREFERENCE_EFFECTS.fieldPlaneDisplayMode).toBe('render-refreshing');
  });

  it('treats everything a solve is submitted with as solve-affecting', () => {
    expect(SOLVE_OPTION_EFFECTS.engine).toBe('solve-affecting');
    expect(SOLVE_OPTION_EFFECTS.symmetry).toBe('solve-affecting');
    expect(SOLVE_OPTION_EFFECTS.frequencyListText).toBe('solve-affecting');
    expect(SOLVE_OPTION_EFFECTS['polar.distance']).toBe('solve-affecting');
    expect(SOLVE_OPTION_EFFECTS['polar.enabledAxes']).toBe('solve-affecting');
    // The run's label travels with the submission, so renaming changes it.
    expect(preferenceEffect('outputName')).toBe('solve-affecting');
    expect(preferenceEffect('counter')).toBe('solve-affecting');
  });

  it('treats chrome and deferred choices as inert', () => {
    expect(SETTINGS_NAMESPACE_EFFECTS.dockviewLayout).toBe('inert');
    expect(SETTINGS_NAMESPACE_EFFECTS.dockviewMode).toBe('inert');
    expect(SETTINGS_NAMESPACE_EFFECTS.paramSections).toBe('inert');
    expect(preferenceEffect('exportFormats')).toBe('inert');
    expect(preferenceEffect('cadApplication')).toBe('inert');
    expect(VIEWER_PREFERENCE_EFFECTS.startupCameraMode).toBe('inert');
  });

  it('classifies a namespace by the strongest effect it can carry', () => {
    // `preferences` is mostly redraws, but the run-naming keys live there too.
    expect(SETTINGS_NAMESPACE_EFFECTS.preferences).toBe('solve-affecting');
    expect(SETTINGS_NAMESPACE_EFFECTS.solveOptions).toBe('solve-affecting');
    expect(SETTINGS_NAMESPACE_EFFECTS.viewer).toBe('render-refreshing');
  });
});
