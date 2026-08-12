import { beforeEach, describe, expect, it } from 'vitest';
import { projectSubmittedDesign } from '../jobs/submittedProjection';
import { preferencesStore } from '../prefs/preferences';
import { designForFamily } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { currentJobLabel, jobAnnouncement } from './JobsCoordinator';

describe('current job naming', () => {
  beforeEach(() => {
    preferencesStore.resetForTests();
    resetDocumentStore();
    resetSolveOptionsStore();
  });

  it('uses the current document filename before a name baseline exists', () => {
    useDocumentStore.getState().setFilename('saved winner.cfg');
    expect(currentJobLabel()).toBe('saved winner');
  });

  it('uses a fresh name committed by the same keyboard-submit event', () => {
    const design = designForFamily('R-OSSE');
    const options = useSolveOptionsStore.getState().options();
    const projection = projectSubmittedDesign(design, options);
    preferencesStore.update({ outputName: 'horn', nameSourceProjection: projection });
    const staleRenderPreferences = preferencesStore.getSnapshot();

    // RunNameField commits before Ctrl/Cmd+Enter bubbles to the coordinator's
    // window shortcut. The submit path must read this store
    // update, not the preferences captured by the previous React render.
    preferencesStore.update({ outputName: 'fresh', nameSourceProjection: projection });

    expect(staleRenderPreferences).toMatchObject({ outputName: 'horn' });
    expect(currentJobLabel(design, options)).toBe('fresh');
  });

  it('decorates the submitted label without changing the stored core', () => {
    preferencesStore.update({
      outputName: 'horn',
      runNameDatePosition: 'prefix',
      runNameDateFormat: 'yyyy-mm-dd',
    });
    expect(currentJobLabel(undefined, undefined, undefined, new Date(2026, 7, 12, 12)))
      .toBe('2026-08-12_horn');
    expect(preferencesStore.getSnapshot().outputName).toBe('horn');
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
