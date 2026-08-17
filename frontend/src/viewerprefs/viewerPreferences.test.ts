import { describe, expect, it, vi } from 'vitest';
import { DEFAULT_VIEWER_PREFERENCES, parseViewerPreferences, VIEWER_PREFERENCES_NAMESPACE, ViewerPreferencesStore } from './viewerPreferences';

describe('viewer preference persistence', () => {
  it('persists every viewer preference beneath one versioned namespace', () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
    };
    const store = new ViewerPreferencesStore(storage);
    store.update({ rotateSpeed: 2.4, liveUpdate: false, startupCameraMode: 'orthographic' });

    expect([...values.keys()]).toEqual([VIEWER_PREFERENCES_NAMESPACE]);
    expect(new ViewerPreferencesStore(storage).getSnapshot()).toMatchObject({
      rotateSpeed: 2.4,
      liveUpdate: false,
      startupCameraMode: 'orthographic',
    });
  });

  it('tolerantly restores defaults for malformed, unknown, and out-of-range fields', () => {
    expect(parseViewerPreferences('{broken')).toEqual(DEFAULT_VIEWER_PREFERENCES);
    expect(parseViewerPreferences(JSON.stringify({
      schemaVersion: 1,
      viewer: { ...DEFAULT_VIEWER_PREFERENCES, rotateSpeed: 99, dampingEnabled: 'yes', unknown: true },
    }))).toMatchObject({
      rotateSpeed: DEFAULT_VIEWER_PREFERENCES.rotateSpeed,
      dampingEnabled: DEFAULT_VIEWER_PREFERENCES.dampingEnabled,
    });
  });

  it('adopts a durable value that arrives after construction and notifies listeners', () => {
    const store = new ViewerPreferencesStore();
    const listener = vi.fn();
    store.subscribe(listener);

    store.adoptDurable(JSON.stringify({
      schemaVersion: 1,
      viewer: { ...DEFAULT_VIEWER_PREFERENCES, rotateSpeed: 2.2, dampingEnabled: false },
    }));

    expect(store.getSnapshot()).toMatchObject({ rotateSpeed: 2.2, dampingEnabled: false });
    expect(listener).toHaveBeenCalledOnce();
  });
});
