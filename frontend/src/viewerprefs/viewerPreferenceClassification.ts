/**
 * The viewer half of the preference classification. See
 * `prefs/preferenceClassification.ts` for what the three effects mean and why
 * this is a source of truth rather than a wiring change.
 *
 * Nothing the viewer remembers reaches the solver, so no key here is
 * solve-affecting; the split that matters is between settings the viewport
 * consumes as it renders and settings it reads once.
 */
import type { PreferenceEffect } from '../prefs/preferenceClassification';
import type { ViewerPreferences } from './viewerPreferences';

export const VIEWER_PREFERENCE_EFFECTS: Record<keyof ViewerPreferences, PreferenceEffect> = {
  // The camera-control settings are props on the live canvas, so a change is a
  // viewport render even though it only shows up on the next interaction.
  rotateSpeed: 'render-refreshing',
  zoomSpeed: 'render-refreshing',
  panSpeed: 'render-refreshing',
  dampingEnabled: 'render-refreshing',
  dampingFactor: 'render-refreshing',
  invertWheelZoom: 'render-refreshing',
  keyboardPanEnabled: 'render-refreshing',
  liveUpdate: 'render-refreshing',
  tintSolvedRegion: 'render-refreshing',
  // Read once, when a viewport mounts, and never again for that viewport.
  startupCameraMode: 'inert',
  fieldPlaneDisplayMode: 'render-refreshing',
  fieldPlaneRangeLocked: 'render-refreshing',
  fieldPlaneAnimationSpeed: 'render-refreshing',
};

export type UnclassifiedViewerPreference = Exclude<keyof typeof VIEWER_PREFERENCE_EFFECTS, keyof ViewerPreferences>;
const _noStrayViewerKeys: UnclassifiedViewerPreference[] = [];
void _noStrayViewerKeys;
