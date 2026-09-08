import { getCadLinkedDesign } from '../api/cadlink';
import { hydrateDesignDocument, openDesignText, type CadLinkOpenState } from '../api/designIo';
import { useDesignStore, type DesignLoadSource } from '../stores/design';
import { documentSettingsSignature } from '../stores/designWire';
import { designNameForOpenedFile } from '../stores/designName';
import { useDocumentStore, type DesignIdentity } from '../stores/document';
import { restoreSolveSettingsFromBlocks } from '../stores/solveOptions';

/** Only the fields a later save may advance; the rest is derived state. */
export function editableIdentity(identity: DesignIdentity | null | undefined): DesignIdentity | null {
  return identity ? {
    designId: identity.designId,
    lineageId: identity.lineageId,
    baseEditVersion: identity.baseEditVersion,
  } : null;
}

export interface OpenedDesign {
  filename: string;
  report: Awaited<ReturnType<typeof openDesignText>>;
  adoptionCandidate: CadLinkOpenState['adoptionCandidate'] | null;
}

/**
 * Put an opened design into the workspace: viewport, solve settings, name, link.
 *
 * Shared rather than duplicated because opening a design is one operation with
 * one meaning wherever it is triggered from — the File menu, or the CAD Link
 * project switcher. A second copy of this sequence would be a second answer to
 * "which design am I working on", which is exactly what the one-design-name
 * work removed.
 */
export function applyOpenedDesign(
  opened: Awaited<ReturnType<typeof openDesignText>>,
  filename: string,
  loadSource: DesignLoadSource = 'ordinary',
): OpenedDesign {
  const openedDesign = hydrateDesignDocument(opened.design);
  useDesignStore.getState().replaceDesign(openedDesign, { loadSource });
  restoreSolveSettingsFromBlocks(openedDesign.extra_blocks);
  const document = useDocumentStore.getState();
  // One name for the whole document: the opened file's, unless the file's own
  // Report.Title is that same name spelled more fully.
  document.setDesignName(designNameForOpenedFile(filename, openedDesign.extra_blocks));
  document.setCadLink(editableIdentity(opened.cadlink?.identity), opened.cadlink?.classification ?? 'missing');
  document.markSaved(useDesignStore.getState().designRevision, documentSettingsSignature());
  return {
    filename,
    report: opened,
    adoptionCandidate: opened.cadlink?.classification === 'missing'
      ? opened.cadlink.adoptionCandidate
      : null,
  };
}

/**
 * Refused because the document this open was decided for is no longer the one
 * on screen. Its own class so a caller can tell it from a failed request: the
 * project is fine and the open can be repeated, nothing went wrong with it.
 */
export class DesignOpenSupersededError extends Error {}

/**
 * Open one project from the CAD-link registry as the working design.
 *
 * `guard` is asked once, synchronously, after the last await and immediately
 * before the store is written — never earlier. Fetching the registry snapshot
 * and parsing it are two network round trips, and a caller that decided it was
 * safe to replace the document before them decided it against a document that
 * may since have been edited, replaced, or closed. Returning a reason from the
 * guard refuses the open with that reason and leaves every store untouched.
 */
export async function openCadLinkedProject(
  designId: string,
  fetcher: typeof fetch = fetch,
  loadSource: DesignLoadSource = 'ordinary',
  guard?: () => string | null,
): Promise<OpenedDesign> {
  const snapshot = await getCadLinkedDesign(designId, fetcher);
  const opened = await openDesignText(snapshot.text, fetcher);
  const refusal = guard?.() ?? null;
  if (refusal) throw new DesignOpenSupersededError(refusal);
  return applyOpenedDesign(opened, snapshot.filename, loadSource);
}
