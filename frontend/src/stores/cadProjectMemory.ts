import { namespaceStorage } from './durableSettings';

/**
 * Which CAD project was on screen last, remembered across sessions.
 *
 * Entering CAD Link mode with nothing selected used to mean an empty screen
 * and a manual trip through the project switcher, every time. The ingest
 * record knows which project its geometry belongs to, so the coordinator
 * notes the lineage here as returns are prepared and reopens that project's
 * newest return when the mode comes back empty.
 *
 * Its own namespace, not the workspace mode's: a namespace holds exactly one
 * value, so sharing one would have each write clobber the other.
 */
const storage = namespaceStorage('cadProject');
const KEY = 'lastCadLineage';

export function rememberCadProject(lineageId: string | null | undefined): void {
  const value = String(lineageId ?? '').trim();
  if (!value) return;
  storage.setItem(KEY, value);
}

export function rememberedCadProject(): string | null {
  const value = storage.getItem(KEY);
  return value && value.trim() ? value : null;
}
