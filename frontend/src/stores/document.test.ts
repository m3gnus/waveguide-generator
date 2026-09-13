import { beforeEach, describe, expect, it } from 'vitest';
import { resetDocumentStore, useDocumentStore, type DesignIdentity } from './document';

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

describe('the design name owns the filename', () => {
  beforeEach(() => resetDocumentStore());

  it('derives the filename from every rename, so the two cannot drift', () => {
    useDocumentStore.getState().setDesignName('  ATH Tritonia-M  ');
    expect(useDocumentStore.getState()).toMatchObject({
      designName: 'ATH Tritonia-M', filename: 'ATH_Tritonia-M.cfg',
    });

    useDocumentStore.getState().setDesignName('Tritonia mk2');
    expect(useDocumentStore.getState().filename).toBe('Tritonia_mk2.cfg');
  });

  it('leaves an untitled document without a filename to claim', () => {
    useDocumentStore.getState().setDesignName('');
    expect(useDocumentStore.getState()).toMatchObject({ designName: '', filename: '' });
  });

  it('stamps the name into the opened-file baseline only when it is established', () => {
    useDocumentStore.getState().setDesignName('winner');
    expect(useDocumentStore.getState().savedDesignName).toBe('');

    useDocumentStore.getState().markSaved(1);
    expect(useDocumentStore.getState().savedDesignName).toBe('winner');
  });
});

describe('the opened-file baseline', () => {
  beforeEach(() => resetDocumentStore());

  it('stamps a settings baseline only when the caller supplies one', () => {
    useDocumentStore.getState().markSaved(7, '{"distance":2}');
    expect(useDocumentStore.getState()).toMatchObject({ savedRevision: 7, savedSettings: '{"distance":2}' });

    // Callers that only know the revision must not silently clear the baseline.
    useDocumentStore.getState().markSaved(8);
    expect(useDocumentStore.getState()).toMatchObject({ savedRevision: 8, savedSettings: '{"distance":2}' });

    resetDocumentStore();
    expect(useDocumentStore.getState().savedSettings).toBeNull();
  });
});
