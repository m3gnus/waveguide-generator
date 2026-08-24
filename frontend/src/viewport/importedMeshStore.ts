import type { ImportedMeshScene } from './importedMesh';

type Listener = () => void;
export type ImportedMeshShowing = 'parametric' | 'cad' | 'file' | 'solver';
export type ImportedMeshSlot = Exclude<ImportedMeshShowing, 'parametric'>;

export interface ImportedMeshState {
  cad: ImportedMeshScene | null;
  file: ImportedMeshScene | null;
  solver: ImportedMeshScene | null;
  showing: ImportedMeshShowing;
}

const EMPTY_STATE: ImportedMeshState = { cad: null, file: null, solver: null, showing: 'parametric' };

/** Viewport alternatives live outside React so CAD Link, file import and the
 * solver-mesh view can retain their independent artifacts while
 * useSyncExternalStore observes one cached snapshot. The generation stays
 * private: intent changes that do not change the snapshot must still make an
 * in-flight load stale. */
class ImportedMeshStore {
  private value: ImportedMeshState = EMPTY_STATE;
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

  setCad(scene: ImportedMeshScene, generation = this.beginIntent(), activate = true): boolean {
    return this.setSlot('cad', scene, generation, activate);
  }

  setFile(scene: ImportedMeshScene, generation = this.beginIntent()): boolean {
    return this.setSlot('file', scene, generation);
  }

  /** Publish a freshly built solver-mesh scene. Unlike the other slots this
   * one refreshes in place, so it must never steal the viewport: it only
   * activates when the solver view is what the user is already looking at. */
  setSolver(scene: ImportedMeshScene, generation = this.beginIntent(), activate = true): boolean {
    return this.setSlot('solver', scene, generation, activate);
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

  showSolver(generation = this.beginIntent()): void {
    this.show('solver', generation);
  }

  clear(slot: ImportedMeshSlot | 'all' = 'all'): void {
    this.beginIntent();
    const cad = slot === 'cad' || slot === 'all' ? null : this.value.cad;
    const file = slot === 'file' || slot === 'all' ? null : this.value.file;
    const solver = slot === 'solver' || slot === 'all' ? null : this.value.solver;
    const cleared = { cad, file, solver };
    const showing = this.value.showing !== 'parametric' && cleared[this.value.showing] === null
      ? 'parametric'
      : this.value.showing;
    if (
      cad === this.value.cad
      && file === this.value.file
      && solver === this.value.solver
      && showing === this.value.showing
    ) return;
    this.publish({ cad, file, solver, showing });
  }

  private setSlot(slot: ImportedMeshSlot, scene: ImportedMeshScene, generation: number, activate = true): boolean {
    if (!this.isCurrentGeneration(generation)) return false;
    const showing = activate ? slot : this.value.showing;
    if (this.value[slot] === scene && this.value.showing === showing) return true;
    this.publish({ ...this.value, [slot]: scene, showing });
    return true;
  }

  private show(showing: ImportedMeshShowing, generation: number): void {
    if (!this.isCurrentGeneration(generation) || showing === this.value.showing) return;
    // An empty solver slot may still be selected: the solver view builds its
    // scene on demand after activation. The other alternatives are loaded
    // before they are offered, so an empty one stays a no-op and `showing`
    // stays truthful, while the generation advance above still rejects a load
    // started for older intent.
    if (showing !== 'parametric' && showing !== 'solver' && this.value[showing] === null) return;
    this.publish({ ...this.value, showing });
  }

  private publish(value: ImportedMeshState): void {
    this.value = value;
    this.listeners.forEach((listener) => listener());
  }
}

export const importedMeshStore = new ImportedMeshStore();
