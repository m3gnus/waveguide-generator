import { create } from 'zustand';

export interface DesignIdentity {
  designId: string;
  lineageId: string;
  baseEditVersion: number;
}

export type CadLinkClassification = 'current' | 'stale_copy' | 'externally_edited' | 'foreign' | 'missing';

export interface DocumentState {
  filename: string;
  savedRevision: number | null;
  identity: DesignIdentity | null;
  classification: CadLinkClassification | null;
  setFilename: (filename: string) => void;
  markSaved: (revision: number) => void;
  setCadLink: (identity: DesignIdentity | null, classification: CadLinkClassification) => void;
  adoptSavedIdentity: (identity: DesignIdentity) => void;
  restoreDocumentState: (state: Pick<DocumentState, 'filename' | 'savedRevision' | 'identity' | 'classification'>) => void;
}

export const useDocumentStore = create<DocumentState>((set) => ({
  filename: 'tritonia_mk2.cfg',
  savedRevision: 1,
  identity: null,
  classification: null,
  setFilename: (filename) => set({ filename }),
  markSaved: (savedRevision) => set({ savedRevision }),
  setCadLink: (identity, classification) => set({ identity, classification }),
  adoptSavedIdentity: (identity) => set({ identity, classification: 'current' }),
  restoreDocumentState: ({ filename, savedRevision, identity, classification }) => set({
    filename,
    savedRevision,
    identity,
    classification,
  }),
}));

export function resetDocumentStore(): void {
  useDocumentStore.setState({
    filename: 'tritonia_mk2.cfg',
    savedRevision: 1,
    identity: null,
    classification: null,
  });
}
