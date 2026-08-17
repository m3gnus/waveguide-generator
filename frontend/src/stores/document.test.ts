import { beforeEach, describe, expect, it } from 'vitest';
import { documentIsUnsaved, resetDocumentStore, useDocumentStore, type DesignIdentity } from './document';

const opened: DesignIdentity = {
  designId: 'wgd_01K00000000000000000000000',
  lineageId: 'wgl_01K00000000000000000000000',
  baseEditVersion: 4,
};

describe('CAD-link document identity', () => {
  beforeEach(() => resetDocumentStore());

  it('tracks open identity and classification, then adopts a committed save', () => {
    useDocumentStore.getState().setCadLink(opened, 'stale_copy');
    expect(useDocumentStore.getState()).toMatchObject({ identity: opened, classification: 'stale_copy' });

    const committed = { ...opened, designId: 'wgd_01K00000000000000000000001', baseEditVersion: 1 };
    useDocumentStore.getState().adoptSavedIdentity(committed);
    expect(useDocumentStore.getState()).toMatchObject({ identity: committed, classification: 'current' });
  });

  it('clears identity and classification for a new document', () => {
    useDocumentStore.getState().setCadLink(opened, 'foreign');
    resetDocumentStore();
    expect(useDocumentStore.getState()).toMatchObject({ identity: null, classification: null });
  });
});

describe('unsaved-changes accounting', () => {
  beforeEach(() => resetDocumentStore());

  it('counts a settings change the geometry revision cannot see', () => {
    // Saved: same revision, same settings.
    expect(documentIsUnsaved(4, 4, '{"distance":2}', '{"distance":2}')).toBe(false);
    // Directivity typed after the save. The revision still matches, because
    // these settings do not rebuild the mesh -- but the file is now stale.
    expect(documentIsUnsaved(4, 4, '{"distance":2}', '{"distance":3}')).toBe(true);
    expect(documentIsUnsaved(5, 4, '{"distance":2}', '{"distance":2}')).toBe(true);
  });

  it('leaves an app nobody has saved in alone', () => {
    // No file to be unsaved against: these settings describe the user's own
    // measurement rig and survive New deliberately, so they must not light the
    // unsaved dot on a window that has never held a document.
    expect(documentIsUnsaved(1, 1, null, '{"distance":3}')).toBe(false);
  });

  it('stamps a settings baseline only when a save supplies one', () => {
    useDocumentStore.getState().markSaved(7, '{"distance":2}');
    expect(useDocumentStore.getState()).toMatchObject({ savedRevision: 7, savedSettings: '{"distance":2}' });

    // Callers that only know the revision must not silently clear the baseline.
    useDocumentStore.getState().markSaved(8);
    expect(useDocumentStore.getState()).toMatchObject({ savedRevision: 8, savedSettings: '{"distance":2}' });

    resetDocumentStore();
    expect(useDocumentStore.getState().savedSettings).toBeNull();
  });
});
