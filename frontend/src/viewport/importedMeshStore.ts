import type { ImportedMeshScene } from './importedMesh';

type Listener = () => void;

/** The imported mesh shown in place of the parametric preview.
 *
 * Lives outside the Viewport component so other panels can drive it: the
 * MSH toolbar button and CAD Link's post-ingest display both set it, and
 * the viewport's Clear control clears it regardless of who set it.
 */
class ImportedMeshStore {
  private value: ImportedMeshScene | null = null;
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): ImportedMeshScene | null => this.value;

  set(scene: ImportedMeshScene | null): void {
    if (scene === this.value) return;
    this.value = scene;
    this.listeners.forEach((listener) => listener());
  }
}

export const importedMeshStore = new ImportedMeshStore();
