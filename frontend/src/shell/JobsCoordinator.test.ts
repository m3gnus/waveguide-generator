import { beforeEach, describe, expect, it } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { currentJobLabel, incrementJobVersion, jobAnnouncement } from './JobsCoordinator';

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

describe('job lifecycle announcements', () => {
  const job = (id: string, status: string, label?: string) => ({ id, status, label }) as never;

  it('says nothing when no run changed state', () => {
    const seen = new Map([['a', 'complete' as const]]);
    expect(jobAnnouncement(seen, [job('a', 'complete')])).toBeNull();
  });

  it('names a run that started, finished, or failed', () => {
    expect(jobAnnouncement(new Map([['a', 'queued' as const]]), [job('a', 'running', 'horn_v19')]))
      .toBe('Solve started: horn_v19.');
    expect(jobAnnouncement(new Map([['a', 'running' as const]]), [job('a', 'complete', 'horn_v19')]))
      .toBe('Solve finished: horn_v19.');
    expect(jobAnnouncement(new Map([['a', 'running' as const]]), [job('a', 'error', 'horn_v19')]))
      .toBe('Solve failed: horn_v19.');
  });

  it('counts rather than lists when several change at once', () => {
    const seen = new Map([['a', 'running' as const], ['b', 'running' as const]]);
    expect(jobAnnouncement(seen, [job('a', 'complete', 'x'), job('b', 'complete', 'y')]))
      .toBe('2 solves finished.');
  });

  // A run the announcer has never seen is history being loaded, not news --
  // otherwise every reload reads the entire run list aloud.
  it('ignores runs it has not seen before', () => {
    expect(jobAnnouncement(new Map(), [job('a', 'complete', 'horn_v01'), job('b', 'error', 'horn_v02')])).toBeNull();
  });
});
