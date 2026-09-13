import { useEffect } from 'react';
import { useDocumentStore } from '../stores/document';

export const APP_NAME = 'Waveguide Generator';

/**
 * What the tab says the window is showing.
 *
 * The title used to be a literal string in index.html naming one specific
 * `.cfg` file, so every window of every install claimed to have that document
 * open forever, whatever was actually on screen.
 *
 * It carries no unsaved bullet: WG has no Save, so there is no saved state for
 * one to mark. Whether replacing the design would lose it is asked when
 * something is about to replace it (`design/replacementCheck.ts`).
 */
export function documentTitle(designName: string): string {
  const name = designName.trim();
  if (!name) return APP_NAME;
  return `${name} — ${APP_NAME}`;
}

export function useDocumentTitle(): void {
  const designName = useDocumentStore((state) => state.designName);
  useEffect(() => {
    document.title = documentTitle(designName);
  }, [designName]);
}
