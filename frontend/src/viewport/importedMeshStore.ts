import type { ImportedMeshScene } from './importedMesh';

type Listener = () => void;

export interface ImportedMeshState {
  scene: ImportedMeshScene | null;
  active: boolean;
}

/** The imported mesh shown in place of the parametric preview.
 *
 * Lives outside the Viewport component so other panels can drive it: the
 * MSH toolbar button and CAD Link's post-ingest display both set it, and
 * the viewport's Clear control clears it regardless of who set it.
 */
class ImportedMeshStore {
  private value: ImportedMeshState = { scene: null, active: false };
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): ImportedMeshState => this.value;

  set(scene: ImportedMeshScene | null): void {
    if (scene === this.value.scene && this.value.active === Boolean(scene)) return;
    this.value = { scene, active: scene !== null };
    this.listeners.forEach((listener) => listener());
  }

  showParametric(): void {
    if (!this.value.active) return;
    this.value = { ...this.value, active: false };
    this.listeners.forEach((listener) => listener());
  }

  showImported(): void {
    if (this.value.scene === null || this.value.active) return;
    this.value = { ...this.value, active: true };
    this.listeners.forEach((listener) => listener());
  }

  clear(): void {
    if (this.value.scene === null && !this.value.active) return;
    this.value = { scene: null, active: false };
    this.listeners.forEach((listener) => listener());
  }
}

export const importedMeshStore = new ImportedMeshStore();
