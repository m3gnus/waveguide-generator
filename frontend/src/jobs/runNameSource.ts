/**
 * Which name a run takes, per workspace.
 *
 * A parametric solve is an event in the life of the WG document, so it takes
 * the document's `designName`. A CAD Link solve is not: its geometry is a
 * Fusion document that WG only ingested, and the parametric design still open
 * behind it may be an unrelated leftover from a previous session. Labelling
 * those runs from `designName` is how a Fusion return ended up filed under the
 * last `.cfg` that happened to be restored from the autosave draft.
 *
 * The Fusion document already states its own name -- it travels in the return
 * manifest as `document.name` and reaches WG as the bundle's `documentName`,
 * which is what the CAD viewport heading has always shown. Reading the run
 * label from the same string is what makes renaming the document in Fusion,
 * before the first solve, name the runs.
 */
import { useSyncExternalStore } from 'react';
import type { CadReturnBundle } from '../api/cadlink';
import { useCadReturnStore } from '../stores/cadReturn';
import { normalizeDesignName } from '../stores/designName';
import { useDocumentStore } from '../stores/document';
import { workspaceModeStore, type WorkspaceMode } from '../stores/workspaceMode';

/** Where the name on the next run comes from, and whether WG can edit it. */
export interface RunNameSource {
  name: string;
  origin: 'design' | 'cad';
}

/**
 * The name a Fusion return states for itself.
 *
 * `documentName` is the manifest's document name and the server already falls
 * back to the bundle stem for it, so `name` only stands in for a bundle whose
 * manifest could not be read at all.
 */
export function cadRunName(bundle: CadReturnBundle | null): string {
  return normalizeDesignName(bundle?.documentName ?? bundle?.name ?? '');
}

export function runNameSourceFor(
  mode: WorkspaceMode,
  bundle: CadReturnBundle | null,
  designName: string,
): RunNameSource {
  if (mode !== 'cad') return { name: designName, origin: 'design' };
  // An unnamed return is still a CAD run: falling back to the parametric name
  // here would reintroduce exactly the borrowing this module exists to stop.
  return { name: cadRunName(bundle), origin: 'cad' };
}

/** Read at submission time, so the label matches the geometry being solved. */
export function currentRunNameSource(): RunNameSource {
  return runNameSourceFor(
    workspaceModeStore.getSnapshot().mode,
    useCadReturnStore.getState().selectedBundle,
    useDocumentStore.getState().designName,
  );
}

export function useRunNameSource(): RunNameSource {
  const mode = useSyncExternalStore(
    workspaceModeStore.subscribe,
    workspaceModeStore.getSnapshot,
    workspaceModeStore.getSnapshot,
  ).mode;
  const bundle = useCadReturnStore((state) => state.selectedBundle);
  const designName = useDocumentStore((state) => state.designName);
  return runNameSourceFor(mode, bundle, designName);
}
