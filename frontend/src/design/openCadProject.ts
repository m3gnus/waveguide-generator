import { getCadLinkedDesign } from '../api/cadlink';
import { hydrateDesignDocument, openDesignText, type CadLinkOpenState } from '../api/designIo';
import { currentDocumentLoad, useDesignStore, type DesignLoadSource } from '../stores/design';
import { documentSettingsSignature } from '../stores/designWire';
import { designNameForOpenedFile } from '../stores/designName';
import { useDocumentStore, type DesignIdentity } from '../stores/document';
import { currentEditorMutation } from '../stores/editorMutation';
import { restoreSolveSettingsFromBlocks } from '../stores/solveOptions';
import { keptContentKeyNow } from './replacementCheck';
import { noteExplicitNavigation } from '../shell/workspaceNavigation';

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
  // Last, once the design, its settings and its name have all applied: the
  // design as opened is one a later replacement can count as kept.
  document.setOpenedContentKey(keptContentKeyNow());
  return {
    filename,
    report: opened,
    adoptionCandidate: opened.cadlink?.classification === 'missing'
      ? opened.cadlink.adoptionCandidate
      : null,
  };
}

/** Which newer event overtook an open before it could apply. */
export type DesignOpenSupersededCode =
  | 'superseded_by_edit'
  | 'superseded_by_newer_open'
  | 'superseded_by_other_load';

/**
 * What an open was decided against, captured the moment it was decided.
 *
 * - `documentLoad`: the document generation (`currentDocumentLoad`). It moves
 *   whenever anything replaces the document: another open, a run recall, New.
 * - `editorMutation`: the editor-wide mutation token (`stores/editorMutation`),
 *   which moves on any change to the design, its name or a file-owned solve
 *   setting. Null when an edit needs no refusing here: the Fusion auto-open
 *   judges it at the same instant by whether anything would be lost, and a
 *   CAD-only project switch replaces no design at all.
 * - `requestId`: this open's place in the order opens were asked for. Only the
 *   most recent may apply, whichever answer arrives last.
 */
export interface DesignOpenTicket {
  readonly documentLoad: number;
  readonly editorMutation: number | null;
  readonly requestId: number;
}

let latestOpenRequest = 0;

/**
 * The ticket for an open. Take it once the replacement has been decided (after
 * any confirmation) and before the open's first await. Taking one makes every
 * open asked for earlier stale, so an open that may still decide not to go
 * ahead takes it only once it has decided.
 *
 * `decidedAgainstLoad` is the document generation the decision was made
 * against, for a decision that itself awaited: a design put on screen during
 * that await is newer than the decision.
 */
export function takeDesignOpenTicket(options: {
  checkEdits?: boolean;
  decidedAgainstLoad?: number;
} = {}): DesignOpenTicket {
  latestOpenRequest += 1;
  // Every ticket is a design or project the user chose to open: from here on,
  // a solve finishing for what was on screen before may not pull them back.
  noteExplicitNavigation();
  return {
    documentLoad: options.decidedAgainstLoad ?? currentDocumentLoad(),
    editorMutation: options.checkEdits === false ? null : currentEditorMutation(),
    requestId: latestOpenRequest,
  };
}

/** Why `ticket` may no longer apply, or null while it still may. Synchronous. */
export function supersededDesignOpen(ticket: DesignOpenTicket): DesignOpenSupersededCode | null {
  if (ticket.requestId !== latestOpenRequest) return 'superseded_by_newer_open';
  // Before the edit check: a load also moves the mutation token.
  if (ticket.documentLoad !== currentDocumentLoad()) return 'superseded_by_other_load';
  if (ticket.editorMutation !== null && ticket.editorMutation !== currentEditorMutation()) {
    return 'superseded_by_edit';
  }
  return null;
}

function supersededMessage(code: DesignOpenSupersededCode, what: string): string {
  if (code === 'superseded_by_edit') {
    return `Did not open ${what}: the design on screen changed while ${what} was loading, and the change was kept. Open ${what} again to replace the design.`;
  }
  if (code === 'superseded_by_newer_open') {
    return `Did not open ${what}: another design was asked for after it.`;
  }
  return `Did not open ${what}: another design was put on screen while ${what} was loading.`;
}

/**
 * Refused because the document this open was decided for is no longer the one
 * on screen. Its own class so a caller can tell it from a failed request: the
 * project is fine and the open can be repeated, nothing went wrong with it.
 * `refused_by_caller` is the caller's own last-instant guard saying no.
 */
export class DesignOpenSupersededError extends Error {
  constructor(
    readonly code: DesignOpenSupersededCode | 'refused_by_caller',
    message: string,
  ) {
    super(message);
    this.name = 'DesignOpenSupersededError';
  }
}

/**
 * Refuse, with the structured reason, an open that may no longer apply. Call
 * it after the open's last await and immediately before anything is written.
 * `what` names the file or project in the message.
 */
export function assertDesignOpenCurrent(ticket: DesignOpenTicket, what: string): void {
  const code = supersededDesignOpen(ticket);
  if (code) throw new DesignOpenSupersededError(code, supersededMessage(code, what));
}

/**
 * Open one project from the CAD-link registry as the working design.
 *
 * The ticket, then `guard`, are asked once, synchronously, after the last
 * await and immediately before the store is written — never earlier. Fetching
 * the registry snapshot and parsing it are two network round trips, and a
 * caller that decided it was safe to replace the document before them decided
 * it against a document that may since have been edited, replaced, or closed,
 * or while a newer open was asked for. A refusal throws
 * `DesignOpenSupersededError` and leaves every store untouched.
 */
export async function openCadLinkedProject(
  designId: string,
  ticket: DesignOpenTicket,
  options: {
    fetcher?: typeof fetch;
    loadSource?: DesignLoadSource;
    guard?: () => string | null;
  } = {},
): Promise<OpenedDesign> {
  const fetcher = options.fetcher ?? fetch;
  const snapshot = await getCadLinkedDesign(designId, fetcher);
  const opened = await openDesignText(snapshot.text, fetcher);
  assertDesignOpenCurrent(ticket, snapshot.filename);
  const refusal = options.guard?.() ?? null;
  if (refusal) throw new DesignOpenSupersededError('refused_by_caller', refusal);
  return applyOpenedDesign(opened, snapshot.filename, options.loadSource ?? 'ordinary');
}
