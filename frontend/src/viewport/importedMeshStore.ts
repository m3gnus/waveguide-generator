import type { ImportedMeshScene } from './importedMesh';

type Listener = () => void;
export type ImportedMeshShowing = 'parametric' | 'cad' | 'file';
export type ImportedMeshSlot = Exclude<ImportedMeshShowing, 'parametric'>;

export interface ImportedMeshState {
  cad: ImportedMeshScene | null;
  file: ImportedMeshScene | null;
  showing: ImportedMeshShowing;
}

/** Viewport alternatives live outside React so CAD Link and file import can
 * retain their independent artifacts while useSyncExternalStore observes one
 * cached snapshot. The generation stays private: intent changes that do not
 * change the snapshot must still make an in-flight load stale. */
class ImportedMeshStore {
  private value: ImportedMeshState = { cad: null, file: null, showing: 'parametric' };
  private generation = 0;
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): ImportedMeshState => this.value;

  /** Begin an async viewport action before its first await. Only that action's
   * token may commit, so returning to an already-selected source also wins a
   * race without manufacturing a new external-store snapshot. */
  beginIntent(): number {
    this.generation += 1;
    return this.generation;
  }

  isCurrentGeneration(generation: number): boolean {
    return generation === this.generation;
  }

  setCad(scene: ImportedMeshScene, generation = this.beginIntent()): boolean {
    return this.setSlot('cad', scene, generation);
  }

  setFile(scene: ImportedMeshScene, generation = this.beginIntent()): boolean {
    return this.setSlot('file', scene, generation);
  }

  showParametric(): void {
    const generation = this.beginIntent();
    this.show('parametric', generation);
  }

  showCad(generation = this.beginIntent()): void {
    this.show('cad', generation);
  }

  showFile(generation = this.beginIntent()): void {
    this.show('file', generation);
  }

  clear(slot: ImportedMeshSlot | 'all' = 'all'): void {
    this.beginIntent();
    const cad = slot === 'cad' || slot === 'all' ? null : this.value.cad;
    const file = slot === 'file' || slot === 'all' ? null : this.value.file;
    const showing = this.value.showing !== 'parametric' && (
      (this.value.showing === 'cad' && cad === null)
      || (this.value.showing === 'file' && file === null)
    ) ? 'parametric' : this.value.showing;
    if (cad === this.value.cad && file === this.value.file && showing === this.value.showing) return;
    this.publish({ cad, file, showing });
  }

  private setSlot(slot: ImportedMeshSlot, scene: ImportedMeshScene, generation: number): boolean {
    if (!this.isCurrentGeneration(generation)) return false;
    if (this.value[slot] === scene && this.value.showing === slot) return true;
    this.publish({ ...this.value, [slot]: scene, showing: slot });
    return true;
  }

  private show(showing: ImportedMeshShowing, generation: number): void {
    if (!this.isCurrentGeneration(generation) || showing === this.value.showing) return;
    // Empty alternatives are a no-op. This keeps `showing` truthful while the
    // generation advance above still rejects a load started for older intent.
    if (showing !== 'parametric' && this.value[showing] === null) return;
    this.publish({ ...this.value, showing });
  }

  private publish(value: ImportedMeshState): void {
    this.value = value;
    this.listeners.forEach((listener) => listener());
  }
}

export const importedMeshStore = new ImportedMeshStore();
