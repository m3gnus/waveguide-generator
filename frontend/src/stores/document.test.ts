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
