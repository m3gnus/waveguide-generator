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
  // Untitled, and clean. The name used to be a specific .cfg -- someone's test
  // fixture -- so a fresh window claimed that document was open when what was
  // on screen was the built-in default design. The saved revision still matches
  // the design store's initial revision, because an untouched default is not
  // unsaved work: making it null instead would light the unsaved dot, and arm
  // the discard-changes prompt, on an app nobody has typed in yet.
  filename: '',
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
    filename: '',
    savedRevision: 1,
    identity: null,
    classification: null,
  });
}
