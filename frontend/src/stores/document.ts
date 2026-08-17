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
  /**
   * `documentSettingsSignature()` as of the last save or open, or null when
   * nothing has been saved or opened yet.
   *
   * Directivity and solver settings are written into the `.cfg` but live in
   * `useSolveOptionsStore`, so `savedRevision` alone cannot see a change to
   * them. Null means there is no file to be unsaved against: a fresh window
   * showing the default design must not light the unsaved dot merely because
   * these settings carry the user's own measurement rig.
   */
  savedSettings: string | null;
  identity: DesignIdentity | null;
  classification: CadLinkClassification | null;
  setFilename: (filename: string) => void;
  markSaved: (revision: number, settings?: string) => void;
  setCadLink: (identity: DesignIdentity | null, classification: CadLinkClassification) => void;
  adoptSavedIdentity: (identity: DesignIdentity) => void;
  restoreDocumentState: (
    state: Pick<DocumentState, 'filename' | 'savedRevision' | 'identity' | 'classification'>
      & Partial<Pick<DocumentState, 'savedSettings'>>,
  ) => void;
}

/** Whether the document on screen differs from the file it was saved as. */
export function documentIsUnsaved(
  revision: number,
  savedRevision: number | null,
  savedSettings: string | null,
  settings: string,
): boolean {
  return revision !== savedRevision || (savedSettings !== null && savedSettings !== settings);
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
  savedSettings: null,
  identity: null,
  classification: null,
  setFilename: (filename) => set({ filename }),
  markSaved: (savedRevision, savedSettings) => set(
    savedSettings === undefined ? { savedRevision } : { savedRevision, savedSettings },
  ),
  setCadLink: (identity, classification) => set({ identity, classification }),
  adoptSavedIdentity: (identity) => set({ identity, classification: 'current' }),
  restoreDocumentState: ({ filename, savedRevision, savedSettings, identity, classification }) => set({
    filename,
    savedRevision,
    savedSettings: savedSettings ?? null,
    identity,
    classification,
  }),
}));

export function resetDocumentStore(): void {
  useDocumentStore.setState({
    filename: '',
    savedRevision: 1,
    savedSettings: null,
    identity: null,
    classification: null,
  });
}
