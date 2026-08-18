import { useSyncExternalStore } from 'react';
import { durableSettings, namespaceStorage } from '../stores/durableSettings';
import type { FieldPlaneDisplayMode } from '../viewport/fieldPlaneColor';

export type CameraProjection = 'perspective' | 'orthographic';

export interface ViewerPreferences {
  rotateSpeed: number;
  zoomSpeed: number;
  panSpeed: number;
  dampingEnabled: boolean;
  dampingFactor: number;
  invertWheelZoom: boolean;
  keyboardPanEnabled: boolean;
  liveUpdate: boolean;
  tintSolvedRegion: boolean;
  startupCameraMode: CameraProjection;
  fieldPlaneDisplayMode: FieldPlaneDisplayMode;
  fieldPlaneRangeLocked: boolean;
  fieldPlaneAnimationSpeed: number;
}

export const VIEWER_PREFERENCES_NAMESPACE = 'wg2.preferences.v1';
export const VIEWER_PREFERENCES_SCHEMA_VERSION = 2;

export const DEFAULT_VIEWER_PREFERENCES: Readonly<ViewerPreferences> = Object.freeze({
  rotateSpeed: 1,
  zoomSpeed: 1,
  panSpeed: 1,
  dampingEnabled: true,
  dampingFactor: 0.05,
  invertWheelZoom: false,
  keyboardPanEnabled: false,
  liveUpdate: true,
  tintSolvedRegion: true,
  // Orthographic projection keeps circles circular on screen and makes small
  // H/V geometry differences inspectable without perspective foreshortening.
  startupCameraMode: 'orthographic',
  fieldPlaneDisplayMode: 'spl',
  fieldPlaneRangeLocked: false,
  fieldPlaneAnimationSpeed: 1,
});

interface PreferenceEnvelope {
  schemaVersion: 1 | typeof VIEWER_PREFERENCES_SCHEMA_VERSION;
  viewer: ViewerPreferences;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;

function finiteInRange(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum;
}

function fieldPlaneDisplayMode(value: unknown): FieldPlaneDisplayMode | null {
  return value === 'spl' || value === 'normalized' || value === 'phase' || value === 'instantaneous'
    ? value
    : null;
}

export function parseViewerPreferences(raw: string | null): ViewerPreferences {
  if (!raw) return { ...DEFAULT_VIEWER_PREFERENCES };
  try {
    const envelope = JSON.parse(raw) as Partial<PreferenceEnvelope>;
    const stored = (envelope?.schemaVersion === 1 || envelope?.schemaVersion === VIEWER_PREFERENCES_SCHEMA_VERSION)
      && envelope.viewer && typeof envelope.viewer === 'object'
      ? envelope.viewer as Partial<ViewerPreferences>
      : {};
    return {
      rotateSpeed: finiteInRange(stored.rotateSpeed, 0.1, 5) ? stored.rotateSpeed : DEFAULT_VIEWER_PREFERENCES.rotateSpeed,
      zoomSpeed: finiteInRange(stored.zoomSpeed, 0.1, 5) ? stored.zoomSpeed : DEFAULT_VIEWER_PREFERENCES.zoomSpeed,
      panSpeed: finiteInRange(stored.panSpeed, 0.1, 5) ? stored.panSpeed : DEFAULT_VIEWER_PREFERENCES.panSpeed,
      dampingEnabled: typeof stored.dampingEnabled === 'boolean' ? stored.dampingEnabled : DEFAULT_VIEWER_PREFERENCES.dampingEnabled,
      dampingFactor: finiteInRange(stored.dampingFactor, 0.01, 0.5) ? stored.dampingFactor : DEFAULT_VIEWER_PREFERENCES.dampingFactor,
      invertWheelZoom: typeof stored.invertWheelZoom === 'boolean' ? stored.invertWheelZoom : DEFAULT_VIEWER_PREFERENCES.invertWheelZoom,
      keyboardPanEnabled: typeof stored.keyboardPanEnabled === 'boolean' ? stored.keyboardPanEnabled : DEFAULT_VIEWER_PREFERENCES.keyboardPanEnabled,
      liveUpdate: typeof stored.liveUpdate === 'boolean' ? stored.liveUpdate : DEFAULT_VIEWER_PREFERENCES.liveUpdate,
      tintSolvedRegion: typeof stored.tintSolvedRegion === 'boolean' ? stored.tintSolvedRegion : DEFAULT_VIEWER_PREFERENCES.tintSolvedRegion,
      startupCameraMode: stored.startupCameraMode === 'orthographic' || stored.startupCameraMode === 'perspective'
        ? stored.startupCameraMode
        : DEFAULT_VIEWER_PREFERENCES.startupCameraMode,
      fieldPlaneDisplayMode: fieldPlaneDisplayMode(stored.fieldPlaneDisplayMode)
        ?? DEFAULT_VIEWER_PREFERENCES.fieldPlaneDisplayMode,
      fieldPlaneRangeLocked: typeof stored.fieldPlaneRangeLocked === 'boolean'
        ? stored.fieldPlaneRangeLocked
        : DEFAULT_VIEWER_PREFERENCES.fieldPlaneRangeLocked,
      fieldPlaneAnimationSpeed: finiteInRange(stored.fieldPlaneAnimationSpeed, 0.1, 4)
        ? stored.fieldPlaneAnimationSpeed
        : DEFAULT_VIEWER_PREFERENCES.fieldPlaneAnimationSpeed,
    };
  } catch {
    return { ...DEFAULT_VIEWER_PREFERENCES };
  }
}

export class ViewerPreferencesStore {
  private preferences: ViewerPreferences;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly storage?: StorageLike) {
    let raw: string | null = null;
    try { raw = storage?.getItem(VIEWER_PREFERENCES_NAMESPACE) ?? null; } catch { /* storage is optional */ }
    this.preferences = parseViewerPreferences(raw);
  }

  getSnapshot = (): ViewerPreferences => this.preferences;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  update(patch: Partial<ViewerPreferences>): void {
    const next = parseViewerPreferences(JSON.stringify({
      schemaVersion: VIEWER_PREFERENCES_SCHEMA_VERSION,
      viewer: { ...this.preferences, ...patch },
    } satisfies PreferenceEnvelope));
    this.preferences = next;
    try {
      this.storage?.setItem(VIEWER_PREFERENCES_NAMESPACE, JSON.stringify({
        schemaVersion: VIEWER_PREFERENCES_SCHEMA_VERSION,
        viewer: next,
      } satisfies PreferenceEnvelope));
    } catch { /* storage is optional */ }
    this.listeners.forEach((listener) => listener());
  }

  /** Adopt a durable value that arrived after this store was constructed. */
  adoptDurable(raw: string | null): void {
    this.preferences = parseViewerPreferences(raw);
    this.listeners.forEach((listener) => listener());
  }

  reset(): void {
    this.update({ ...DEFAULT_VIEWER_PREFERENCES });
  }
}

export const viewerPreferences = new ViewerPreferencesStore(namespaceStorage('viewer'));
durableSettings.subscribe('viewer', (raw) => viewerPreferences.adoptDurable(raw));

export function useViewerPreferences(): ViewerPreferences {
  return useSyncExternalStore(viewerPreferences.subscribe, viewerPreferences.getSnapshot, viewerPreferences.getSnapshot);
}
