export type WorkspaceMode = 'parametric' | 'cad';

type Listener = () => void;

export interface WorkspaceModeState {
  mode: WorkspaceMode;
}

/** Autosave restores the document before React mounts, but a CAD return only
 * lives for the current session. Persisting `cad` would therefore describe
 * geometry that a reload cannot restore, so every module load starts honest. */
class WorkspaceModeStore {
  private value: WorkspaceModeState = { mode: 'parametric' };
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): WorkspaceModeState => this.value;

  setMode = (mode: WorkspaceMode): void => {
    if (mode === this.value.mode) return;
    this.value = { mode };
    this.listeners.forEach((listener) => listener());
  };
}

export const workspaceModeStore = new WorkspaceModeStore();
