/**
 * The two questions a manual export asks, from code that is not a component.
 *
 * The export entry points are click handlers deep in three unrelated panels;
 * the dialog is mounted once, in the top bar. This is the seam between them:
 * *where does this go?* before anything is built, and *replace these?* when the
 * chosen folder already holds files of the same names whose bytes differ.
 *
 * Neither falls back to acting. An unanswerable destination request fails
 * loudly, and an unanswerable replace request answers "no": a manual export
 * that quietly wrote somewhere the user was never shown, or over files WG did
 * not put there, is the behaviour this whole feature replaces.
 */
import type { ExportCollision } from '../api/exportDestination';

export interface ExportDestinationChoice {
  /** The handle `write-export` accepts. */
  token: string;
  /** The folder it names, for the message that reports where files landed. */
  directory: string;
}

export interface ExportDestinationRequest {
  /** What is being exported, in the dialog's title: "Export STEP". */
  title: string;
  /** The file, or the count of them, when the caller knows it. */
  detail?: string;
}

export type ExportDestinationPrompt = (
  request: ExportDestinationRequest,
) => Promise<ExportDestinationChoice | null>;

/** The second question, asked only when an export would replace something. */
export type ExportReplacementPrompt = (collision: ExportCollision) => Promise<boolean>;

let prompt: ExportDestinationPrompt | null = null;
let replacementPrompt: ExportReplacementPrompt | null = null;

/** Register the mounted dialog, or clear it on unmount. Returns nothing. */
export function provideExportDestinationPrompt(next: ExportDestinationPrompt | null): void {
  prompt = next;
}

/** Register the mounted dialog's replace question, or clear it on unmount. */
export function provideExportReplacementPrompt(next: ExportReplacementPrompt | null): void {
  replacementPrompt = next;
}

/**
 * Ask whether to replace the files an export collides with.
 *
 * One question for the whole export, and only about files whose bytes would
 * change: what is missing is written and what is already identical is a no-op,
 * so neither reaches this. With no dialog mounted the answer is **no** -- an
 * unanswerable question must not be read as consent to overwrite a folder WG
 * does not own.
 */
export function askToReplaceExports(collision: ExportCollision): Promise<boolean> {
  return replacementPrompt ? replacementPrompt(collision) : Promise.resolve(false);
}

/** Ask where this export goes. `null` means the user cancelled -- write nothing. */
export function askForExportDestination(
  request: ExportDestinationRequest,
): Promise<ExportDestinationChoice | null> {
  if (!prompt) {
    return Promise.reject(new Error(
      'The export destination dialog is not available, so no folder could be chosen.',
    ));
  }
  return prompt(request);
}

/** The message every entry point shows when the dialog was dismissed. */
export const EXPORT_CANCELLED_MESSAGE = 'Export cancelled. No files were written.';

/**
 * What to add to an export's message when it replaced files that were there.
 *
 * A chosen folder is the user's own, not a WG-managed one, so "3 files written"
 * on its own can hide that one of them used to be something else.
 */
export function replacedNotice(replaced: readonly string[] | undefined): string {
  if (!replaced?.length) return '';
  return ` Replaced ${replaced.length} existing file${replaced.length === 1 ? '' : 's'}.`;
}
