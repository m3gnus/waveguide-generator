/** A Prepare simulation the server held at the triangle ceiling: the browser
 * path's counterpart of a held Fusion request (`mesh_too_large`). The held
 * request pop-up presents it with the same words and Solve anyway. */
export interface MeshCeilingChoice {
  /** The CAD model's name, as the pop-up titles it. */
  name: string;
  /** The server's next step for the ceiling (change the mesh resolution, or check the model size). */
  nextStep: string;
  /** The selected return this choice answers; a newer selection retires it. */
  bundlePath: string;
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
  ask(next: MeshCeilingChoice): void { choice = next; emit(); },
  clear(): void {
    if (!choice) return;
    choice = null;
    emit();
  },
};
