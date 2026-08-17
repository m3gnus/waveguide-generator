/**
 * One answer to "does this document have unsaved work?".
 *
 * The unsaved dot, the window title, and the discard-changes prompt used to
 * compare the geometry revision against the saved one directly, and so agreed
 * with each other while all three missed the same thing: the directivity and
 * solver settings a `.cfg` also carries. Sharing this hook keeps them from
 * drifting apart again.
 */
import { useDesignStore } from './design';
import { documentSettingsSignature } from './designWire';
import { documentIsUnsaved, useDocumentStore } from './document';
import { useSolveOptionsStore } from './solveOptions';

export function useUnsavedChanges(): boolean {
  const revision = useDesignStore((state) => state.designRevision);
  const savedRevision = useDocumentStore((state) => state.savedRevision);
  const savedSettings = useDocumentStore((state) => state.savedSettings);
  // A string selector, so zustand's identity comparison is a value comparison
  // and a re-serialization that produces the same settings does not re-render.
  const settings = useSolveOptionsStore(documentSettingsSignature);
  return documentIsUnsaved(revision, savedRevision, savedSettings, settings);
}
