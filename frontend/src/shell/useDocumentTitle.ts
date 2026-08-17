import { useEffect } from 'react';
import { useDocumentStore } from '../stores/document';
import { useUnsavedChanges } from '../stores/unsavedChanges';

export const APP_NAME = 'Waveguide Generator';

/**
 * What the tab says the window is showing.
 *
 * The title used to be a literal string in index.html naming one specific
 * `.cfg` file, so every window of every install claimed to have that document
 * open forever, whatever was actually on screen.
 *
 * An unsaved document leads with a bullet, which is the platform convention and
 * the same fact the file chip's amber dot carries -- worth repeating in the tab
 * because the tab is what the user sees when the window is behind another one.
 */
export function documentTitle(filename: string, unsaved: boolean): string {
  const name = filename.trim();
  if (!name) return APP_NAME;
  return `${unsaved ? '• ' : ''}${name} — ${APP_NAME}`;
}

export function useDocumentTitle(): void {
  const filename = useDocumentStore((state) => state.filename);
  const unsaved = useUnsavedChanges();
  useEffect(() => {
    document.title = documentTitle(filename, unsaved);
  }, [filename, unsaved]);
}
