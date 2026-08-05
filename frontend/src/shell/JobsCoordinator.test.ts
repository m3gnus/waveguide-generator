import { beforeEach, describe, expect, it } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { incrementJobVersion } from './JobsCoordinator';

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
});
