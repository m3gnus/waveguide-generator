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
});

describe('permanent document identity', () => {
  it('mints on New and Duplicate, and preserves identity on rename', () => {
    resetDocumentStore();
    const original = useDocumentStore.getState().designUid;
    expect(original).toMatch(/^wgu_[0-7][0-9A-HJKMNP-TV-Z]{25}$/);
    useDocumentStore.getState().setDesignName('Renamed');
    expect(useDocumentStore.getState().designUid).toBe(original);
    useDocumentStore.getState().setCadLink(opened, 'current');
    useDocumentStore.getState().duplicateAsNewDesign();
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'Renamed', identity: null, openedContentKey: null });
    expect(useDocumentStore.getState().designUid).not.toBe(original);
    const duplicate = useDocumentStore.getState().designUid;
    resetDocumentStore();
    expect(useDocumentStore.getState().designUid).not.toBe(duplicate);
  });
});

it('keeps the old unstamped name through rename, and clears the legacy alias for a deliberate duplicate', () => {
  resetDocumentStore();
  useDocumentStore.getState().setLegacyDesignName('original');
  useDocumentStore.getState().setDesignName('renamed');
  expect(useDocumentStore.getState().legacyDesignName).toBe('original');
  useDocumentStore.getState().duplicateAsNewDesign();
  expect(useDocumentStore.getState().legacyDesignName).toBeUndefined();
});
