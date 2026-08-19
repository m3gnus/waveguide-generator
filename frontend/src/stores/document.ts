import { create } from 'zustand';
import { designFilename, normalizeDesignName } from './designName';

export interface DesignIdentity {
  designId: string;
  lineageId: string;
  baseEditVersion: number;
}

export type CadLinkClassification = 'current' | 'stale_copy' | 'externally_edited' | 'foreign' | 'missing';

export interface DocumentState {
  /**
   * The design's one name, as the user typed it.
   *
   * Empty means untitled. Everything that used to carry a name of its own --
   * the filename, the run label, the export stems, the CAD-link name, and the
   * `Report.Title` in the file -- is derived from this.
   */
  designName: string;
  /**
   * `designFilename(designName)`, or '' while untitled.
   *
   * Kept in the state rather than computed at each read so the many existing
   * filename subscribers are unchanged, but it is never set on its own: only
   * `setDesignName` and `restoreDocumentState` write it, which is what makes
   * a name and a filename unable to drift apart.
   */
  filename: string;
  savedRevision: number | null;
  /** The name the document last saved under; a rename is unsaved work. */
  savedDesignName: string;
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
  setDesignName: (name: string) => void;
  markSaved: (revision: number, settings?: string) => void;
  setCadLink: (identity: DesignIdentity | null, classification: CadLinkClassification) => void;
  adoptSavedIdentity: (identity: DesignIdentity) => void;
  restoreDocumentState: (
    state: Pick<DocumentState, 'savedRevision' | 'identity' | 'classification'>
      & Partial<Pick<DocumentState, 'designName' | 'savedSettings' | 'savedDesignName'>>,
  ) => void;
}

/** Whether the document on screen differs from the file it was saved as. */
export function documentIsUnsaved(
  revision: number,
  savedRevision: number | null,
  savedSettings: string | null,
  settings: string,
  designName = '',
  savedDesignName = designName,
): boolean {
  return revision !== savedRevision
    || (savedSettings !== null && savedSettings !== settings)
    || designName !== savedDesignName;
}

export const useDocumentStore = create<DocumentState>((set) => ({
  // Untitled, and clean. The name used to be a specific .cfg -- someone's test
  // fixture -- so a fresh window claimed that document was open when what was
  // on screen was the built-in default design. The saved revision still matches
  // the design store's initial revision, because an untouched default is not
  // unsaved work: making it null instead would light the unsaved dot, and arm
  // the discard-changes prompt, on an app nobody has typed in yet.
  designName: '',
  filename: '',
  savedRevision: 1,
  savedDesignName: '',
  savedSettings: null,
  identity: null,
  classification: null,
  setDesignName: (name) => {
    const designName = normalizeDesignName(name);
    set({ designName, filename: designName ? designFilename(designName) : '' });
  },
  markSaved: (savedRevision, savedSettings) => set((state) => ({
    savedRevision,
    savedDesignName: state.designName,
    ...(savedSettings === undefined ? {} : { savedSettings }),
  })),
  setCadLink: (identity, classification) => set({ identity, classification }),
  adoptSavedIdentity: (identity) => set({ identity, classification: 'current' }),
  restoreDocumentState: ({ designName, savedRevision, savedSettings, savedDesignName, identity, classification }) => {
    const name = normalizeDesignName(designName);
    set({
      designName: name,
      filename: name ? designFilename(name) : '',
      savedRevision,
      savedDesignName: normalizeDesignName(savedDesignName ?? name),
      savedSettings: savedSettings ?? null,
      identity,
      classification,
    });
  },
}));

export function resetDocumentStore(): void {
  useDocumentStore.setState({
    designName: '',
    filename: '',
    savedRevision: 1,
    savedDesignName: '',
    savedSettings: null,
    identity: null,
    classification: null,
  });
}
