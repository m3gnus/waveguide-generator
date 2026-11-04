import { create } from 'zustand';
import {
  getWorkspaceFolder,
  openWorkspaceFolder,
  selectWorkspaceFolder,
  type WorkspaceFolder,
} from '../api/workspace';
import { failureWords } from '../api/httpError';
import { activityStore } from '../shell/activity';

/** Effective Runs folder shared by the run list and CAD project menu.
 * Selecting changes the root while keeping migrated overrides in place.
 */
export interface WorkspaceFolderStore extends WorkspaceFolder {
  /** Undefined until the first read answers; the surfaces say "Loading…". */
  loaded: boolean;
  busy: 'open' | 'select' | null;
  error: string | null;
  load(): Promise<void>;
  open(): Promise<void>;
  /** Resolves to whether the folder actually changed (a picker can be cancelled). */
  select(path?: string): Promise<boolean>;
  resetForTests(): void;
}

/** Ordered against every writer, so a slow read cannot revive a stale path. */
let generation = 0;
let loading: Promise<void> | null = null;
let mutation: Promise<WorkspaceFolder> | null = null;

function message(error: unknown): string {
  return failureWords(error);
}

export const useWorkspaceFolderStore = create<WorkspaceFolderStore>((set) => ({
  path: null,
  selected: false,
  loaded: false,
  busy: null,
  error: null,

  load() {
    // A newly mounted consumer shares the writer's folder response. Its read
    // must not supersede that writer's result or ownership of the busy flag.
    if (mutation) return mutation.then(() => {}, () => {});
    // Three mounted surfaces would otherwise ask three times on every open.
    if (loading) return loading;
    const request = ++generation;
    loading = getWorkspaceFolder().then(
      (folder) => { if (request === generation) set({ ...folder, loaded: true, error: null }); },
      (reason: unknown) => { if (request === generation) set({ loaded: true, error: message(reason) }); },
    ).finally(() => { loading = null; });
    return loading;
  },

  async open() {
    const request = ++generation;
    set({ busy: 'open', error: null });
    const pending = activityStore.track({ kind: 'folder-open', label: 'Opening the Runs folder' }, () => openWorkspaceFolder());
    mutation = pending;
    try {
      const folder = await pending;
      // Only the path: this response says nothing about whether the folder was
      // explicitly chosen, and spreading it would report the default as chosen.
      if (request === generation) set({ path: folder.path, loaded: true });
    } catch (reason) {
      if (request === generation) set({ error: message(reason) });
    } finally {
      if (mutation === pending) mutation = null;
      if (request === generation) set({ busy: null });
    }
  },

  async select(path?: string) {
    const request = ++generation;
    set({ busy: 'select', error: null });
    const pending = activityStore.track({ kind: 'folder-select', label: 'Choosing the WG folder' }, () => selectWorkspaceFolder(path));
    mutation = pending;
    try {
      const folder = await pending;
      if (request === generation) set({ ...folder, loaded: true });
      return folder.selected;
    } catch (reason) {
      if (request === generation) set({ error: message(reason) });
      return false;
    } finally {
      if (mutation === pending) mutation = null;
      if (request === generation) set({ busy: null });
    }
  },

  resetForTests() {
    generation += 1;
    loading = null;
    mutation = null;
    set({ path: null, selected: false, loaded: false, busy: null, error: null });
  },
}));

export function resetWorkspaceFolderStore(): void {
  useWorkspaceFolderStore.getState().resetForTests();
}


/** Publish a WG folder choice before any older Runs-folder read can return. */
export function applyWorkspaceFolderChoice(folder: WorkspaceFolder): void {
  generation += 1;
  loading = null;
  mutation = null;
  useWorkspaceFolderStore.setState({ ...folder, loaded: true, busy: null, error: null });
}
