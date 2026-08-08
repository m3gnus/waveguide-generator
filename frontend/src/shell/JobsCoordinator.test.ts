import { beforeEach, describe, expect, it } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { currentJobLabel, incrementJobVersion } from './JobsCoordinator';

describe('job version sequencing', () => {
  beforeEach(() => preferencesStore.resetForTests());

  it('increments the latest preference snapshot instead of a stale render value', () => {
    preferencesStore.update({ jobVersion: 7 });
    const staleRenderVersion = preferencesStore.getSnapshot().jobVersion;
    preferencesStore.update({ jobVersion: 12 });

    incrementJobVersion();

    expect(staleRenderVersion).toBe(7);
    expect(preferencesStore.getSnapshot().jobVersion).toBe(13);
  });

  it('uses a fresh name committed by the same keyboard-submit event', () => {
    preferencesStore.update({ outputName: 'horn', jobVersion: 15, datePrefix: false });
    const staleRenderPreferences = preferencesStore.getSnapshot();

    // RunNameField commits its free version before Ctrl/Cmd+Enter bubbles to
    // the coordinator's window shortcut. The submit path must read this store
    // update, not the preferences captured by the previous React render.
    preferencesStore.update({ outputName: 'fresh', jobVersion: 1 });

    expect(staleRenderPreferences).toMatchObject({ outputName: 'horn', jobVersion: 15 });
    expect(currentJobLabel()).toBe('fresh_v01');
  });
});
