import { beforeEach, describe, expect, it } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { currentJobLabel, jobAnnouncement } from './JobsCoordinator';

describe('current job naming', () => {
  beforeEach(() => {
    preferencesStore.resetForTests();
    resetDocumentStore();
    resetSolveOptionsStore();
  });

  it('labels the run with the document design name', () => {
    useDocumentStore.getState().setDesignName('saved winner');
    expect(currentJobLabel()).toBe('saved winner1');
  });

  it('uses a rename committed by the same keyboard-submit event', () => {
    useDocumentStore.getState().setDesignName('horn');
    // The Design name field commits before Ctrl/Cmd+Enter bubbles to the
    // coordinator's window shortcut. The submit path must read the store,
    // not the value captured by the previous React render.
    useDocumentStore.getState().setDesignName('fresh');
    expect(currentJobLabel()).toBe('fresh1');
  });

  it('decorates the submitted label without renaming the design', () => {
    useDocumentStore.getState().setDesignName('horn');
    preferencesStore.update({
      runNameDatePosition: 'prefix',
      runNameDateFormat: 'yyyy-mm-dd',
      runNameNumberPosition: 'off',
    });
    expect(currentJobLabel(undefined, new Date(2026, 7, 12, 12))).toBe('2026-08-12_horn');
    expect(useDocumentStore.getState().designName).toBe('horn');
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
