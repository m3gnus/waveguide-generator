import { useEffect } from 'react';
import { useWorkspaceFolderStore } from '../stores/workspaceFolder';

/**
 * The folder WG writes into, wherever it is shown.
 *
 * Selection is the server's native picker (v1's mechanism), never the browser's
 * directory picker: that one exists only in Chromium, and WG is routinely
 * reached from Safari and Firefox. The typed fallback covers the remaining
 * case, a browser on a different machine from the server, where a picker on the
 * server's screen is no help at all.
 */
export function useWorkspaceFolder() {
  const store = useWorkspaceFolderStore();
  useEffect(() => { void store.load(); }, [store.load]);
  return store;
}
