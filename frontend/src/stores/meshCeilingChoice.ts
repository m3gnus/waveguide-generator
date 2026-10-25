import type { CadMeshLimit } from '../api/cadlink';

/** A Prepare simulation the server held at the triangle ceiling: the browser
 * path's counterpart of a held Fusion request (`mesh_too_large`). The held
 * request pop-up presents it with the same words. */
export interface MeshCeilingChoice {
  /** The CAD model's name, as the pop-up titles it. */
  name: string;
  /** The server's next step for the ceiling (change the mesh resolution, or check the model size). */
  nextStep: string;
  /** What the model meshes to against WG's limit, as the server measured it; null when it did not say. */
  meshLimit?: CadMeshLimit | null;
  /** The selected return this choice answers; it never applies to another one. */
  bundlePath: string;
  /** "Later": the pop-up is closed, and the model waits for the choice. */
  deferred: boolean;
}

let choice: MeshCeilingChoice | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

export const meshCeilingChoice = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  getSnapshot(): MeshCeilingChoice | null { return choice; },
  ask(next: Omit<MeshCeilingChoice, 'deferred'>): void {
    choice = { ...next, deferred: false };
    emit();
  },
  /** Later: keep the question for this model, without the pop-up. */
  defer(): void {
    if (!choice || choice.deferred) return;
    choice = { ...choice, deferred: true };
    emit();
  },
  /** Open the deferred question again. */
  reopen(): void {
    if (!choice || !choice.deferred) return;
    choice = { ...choice, deferred: false };
    emit();
  },
  clear(): void {
    if (!choice) return;
    choice = null;
    emit();
  },
};
