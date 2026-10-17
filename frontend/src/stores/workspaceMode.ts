import { namespaceStorage } from './durableSettings';

export type WorkspaceMode = 'parametric' | 'cad';

type Listener = () => void;

export interface WorkspaceModeState {
  mode: WorkspaceMode;
  importedInput: 'cad' | 'file';
  inputRevision: number;
}

const storage = namespaceStorage('workspaceMode');
const STORAGE_KEY = 'mode';
const inputStorage = namespaceStorage('importedInput');

function storedMode(): WorkspaceMode {
  return storage.getItem(STORAGE_KEY) === 'cad' ? 'cad' : 'parametric';
}

/** The workspace comes back the way it was left. Reloading in CAD Link mode
 * used to fall back to Parametric on the grounds that a CAD return could not
 * be restored; the coordinator now reselects the latest matching return on
 * load and a CAD-only project can be reopened from its archive, so starting
 * over was the dishonest option. */
class WorkspaceModeStore {
  private value: WorkspaceModeState = { mode: storedMode(), importedInput: inputStorage.getItem('input') === 'file' ? 'file' : 'cad', inputRevision: 0 };
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): WorkspaceModeState => this.value;

  /** Explicit input intent survives pending/failed loads and workspace switches. */
  setImportedInput = (importedInput: 'cad' | 'file'): void => {
    this.value = { ...this.value, importedInput, inputRevision: this.value.inputRevision + 1 };
    inputStorage.setItem('input', importedInput);
    this.listeners.forEach(listener => listener());
  };

  setMode = (mode: WorkspaceMode): void => {
    if (mode === this.value.mode) return;
    this.value = { ...this.value, mode };
    storage.setItem(STORAGE_KEY, mode);
    this.listeners.forEach((listener) => listener());
  };
}

export const workspaceModeStore = new WorkspaceModeStore();
