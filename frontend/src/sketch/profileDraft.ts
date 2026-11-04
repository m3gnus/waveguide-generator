import { useSyncExternalStore } from 'react';
import { currentDocumentLoad, useDesignStore } from '../stores/design';
import { parameterPanelValidity } from '../design/parameterPanelValidity';
import { openProfileCanvas } from './profileCanvasVisibility';

interface ProfileDraft {
  load: number;
  message: string;
  open(): void;
  revert(): void;
}
const defaultOwner = Symbol('profile-draft');
const drafts = new Map<symbol, ProfileDraft>();
const listeners = new Set<() => void>();
function publish() { listeners.forEach(listener => listener()); }
export function setProfileDraft(value: Omit<ProfileDraft, 'load'> | null, owner = defaultOwner) {
  if (value) drafts.set(owner, { ...value, load: currentDocumentLoad() }); else drafts.delete(owner);
  publish();
}
export function getProfileDraft() {
  const formula = useDesignStore.getState().design.formula;
  if (formula !== 'FREEFORM' && formula !== 'LOOKUP') return null;
  return [...drafts.values()].find(draft => draft.load === currentDocumentLoad()) ?? (parameterPanelValidity.getSnapshot() ? {
    load: currentDocumentLoad(), message: 'Custom profile has an invalid draft. Open sketch or Revert to last valid before Solve.',
    open: openProfileCanvas, revert: () => revertProfileFieldDrafts(),
  } : null);
}
/** Use NumberField's own cancellation; never replace a draft by a guessed value. */
export function revertProfileFieldDrafts(root: ParentNode = document) {
  root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input[aria-invalid="true"], textarea[aria-invalid="true"]').forEach(input =>
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
}
export function profileDraftBlocker() { return getProfileDraft()?.message ?? null; }
export function assertProfileFinished() { const message = profileDraftBlocker(); if (message) throw new Error(message); }
export function useProfileDraftBlocker() {
  return useSyncExternalStore(listener => {
    listeners.add(listener); const unsubscribe = parameterPanelValidity.subscribe(listener);
    return () => { listeners.delete(listener); unsubscribe(); };
  }, profileDraftBlocker, profileDraftBlocker);
}
