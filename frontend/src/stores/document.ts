import { mintDesignUid } from './designUid';
import { create } from 'zustand';
import { designFilename, normalizeDesignName } from './designName';
import { advanceEditorMutation } from './editorMutation';

export interface DesignIdentity {
  designId: string;
  lineageId: string;
  baseEditVersion: number;
}

export type CadLinkClassification = 'current' | 'stale_copy' | 'externally_edited' | 'foreign' | 'missing';

export interface DocumentState {
  designUid?: string;
  /** Original undecorated name of an unstamped import, for F2’s first folder claim. */
  legacyDesignName?: string;
  setLegacyDesignName: (name: string | undefined) => void;
  setDesignUid: (uid: string) => void;
  duplicateAsNewDesign: () => void;
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
  /**
   * The content key of the design as WG last opened it (captured once the open
   * had fully applied), or as WG itself last wrote it somewhere it can be
   * opened from again: Export a copy, or the registry a Send to CAD commits
   * to. Null when there is no such copy.
   *
   * One of the things a replacement check counts as "kept elsewhere" (see
   * `design/replacementCheck.ts`). Autosave records it beside the draft and
   * restores that recorded key; it never derives one from the draft, which is
   * the only copy of whatever it holds.
   */
  openedContentKey: string | null;
  identity: DesignIdentity | null;
  classification: CadLinkClassification | null;
  setDesignName: (name: string) => void;
  setOpenedContentKey: (key: string | null) => void;
  setCadLink: (identity: DesignIdentity | null, classification: CadLinkClassification) => void;
  adoptSavedIdentity: (identity: DesignIdentity) => void;
  restoreDocumentState: (
    state: Pick<DocumentState, 'identity' | 'classification'>
      & Partial<Pick<DocumentState, 'designName' | 'openedContentKey' | 'designUid' | 'legacyDesignName'>>,
  ) => void;
}

export const useDocumentStore = create<DocumentState>((set) => ({
  // Untitled. The name used to be a specific .cfg -- someone's test fixture --
  // so a fresh window claimed that document was open when what was on screen
  // was the built-in default design.
  designUid: mintDesignUid(),
  setDesignUid: (designUid) => set({ designUid }),
  setLegacyDesignName: (legacyDesignName) => set({ legacyDesignName }),
  duplicateAsNewDesign: () => {
    set({ designUid: mintDesignUid(), legacyDesignName: undefined, identity: null, classification: null, openedContentKey: null });
    advanceEditorMutation();
  },
  designName: '',
  filename: '',
  openedContentKey: null,
  identity: null,
  classification: null,
  setDesignName: (name) => {
    const designName = normalizeDesignName(name);
    set({ designName, filename: designName ? designFilename(designName) : '' });
  },
  setOpenedContentKey: (openedContentKey) => set({ openedContentKey }),
  setCadLink: (identity, classification) => set({ identity, classification }),
  adoptSavedIdentity: (identity) => set({ identity, classification: 'current' }),
  restoreDocumentState: ({
    designName, openedContentKey, identity, classification, designUid, legacyDesignName,
  }) => {
    const name = normalizeDesignName(designName);
    set({
      designUid: designUid ?? mintDesignUid(),
      legacyDesignName: designUid ? legacyDesignName : name || undefined,
      designName: name,
      filename: name ? designFilename(name) : '',
      // Only ever the key the caller recorded beside the draft, never one
      // derived from the draft being restored: a draft is the only copy of
      // what it holds. Without a recorded key it counts as kept nowhere.
      openedContentKey: openedContentKey ?? null,
      identity,
      classification,
    });
  },
}));

// The name is part of the document a file records, so a rename is newer than
// any open decided before it (see `editorMutation.ts`) -- however it was written.
useDocumentStore.subscribe((state, previous) => {
  if (state.designName !== previous.designName) advanceEditorMutation();
});

export function resetDocumentStore(): void {
  useDocumentStore.setState({
    designUid: mintDesignUid(),
    legacyDesignName: undefined,
    designName: '',
    filename: '',
    openedContentKey: null,
    identity: null,
    classification: null,
  });
}
