import { create } from 'zustand';
import {
  getWorkspaceFolder,
  openWorkspaceFolder,
  selectWorkspaceFolder,
  type WorkspaceFolder,
} from '../api/workspace';

/**
 * One folder, shown in three places.
 *
 * Settings names it twice -- once as the output workspace, once as the folder
 * CAD projects are archived in -- and the CAD Link panel shows it beside the
 * project it belongs to. They are the same setting, so they share this state:
 * choosing a new folder in any of them must not leave the other two quoting the
 * old path until the dialog is next opened.
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

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useWorkspaceFolderStore = create<WorkspaceFolderStore>((set) => ({
  path: null,
  selected: false,
  loaded: false,
  busy: null,
  error: null,

  load() {
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
    try {
      const folder = await openWorkspaceFolder();
      // Only the path: this response says nothing about whether the folder was
      // explicitly chosen, and spreading it would report the default as chosen.
      if (request === generation) set({ path: folder.path, loaded: true });
    } catch (reason) {
      if (request === generation) set({ error: message(reason) });
    } finally {
      if (request === generation) set({ busy: null });
    }
  },

  async select(path?: string) {
    const request = ++generation;
    set({ busy: 'select', error: null });
    try {
      const folder = await selectWorkspaceFolder(path);
      if (request === generation) set({ ...folder, loaded: true });
      return folder.selected;
    } catch (reason) {
      if (request === generation) set({ error: message(reason) });
      return false;
    } finally {
      if (request === generation) set({ busy: null });
    }
  },

  resetForTests() {
    generation += 1;
    loading = null;
    set({ path: null, selected: false, loaded: false, busy: null, error: null });
  },
}));

export function resetWorkspaceFolderStore(): void {
  useWorkspaceFolderStore.getState().resetForTests();
}
