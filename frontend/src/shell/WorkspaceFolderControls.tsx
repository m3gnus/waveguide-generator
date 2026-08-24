import { useEffect, useState } from 'react';
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

export function WorkspaceFolderControls({ note, manual = false, selectLabel = 'Select folder…', className = '' }: {
  note?: React.ReactNode;
  manual?: boolean;
  selectLabel?: string;
  className?: string;
}) {
  const { path, loaded, busy, error, open, select } = useWorkspaceFolder();
  const [typed, setTyped] = useState('');

  const useTyped = async () => {
    if (await select(typed.trim())) setTyped('');
  };

  return <div className={className} aria-busy={busy !== null}>
    <p className={`workspace-settings-path ${path ? '' : 'not-selected'}`} title={path ?? undefined}>
      {path ?? (loaded ? 'Unavailable' : 'Loading…')}
    </p>
    {note && <p className="cad-settings-note">{note}</p>}
    <div className="settings-theme-options">
      <button disabled={!path || busy !== null} onClick={() => void open()}>Open folder</button>
      <button disabled={busy !== null} onClick={() => void select()}>{selectLabel}</button>
    </div>
    {manual && <details className="cad-folder-manual">
      <summary>Enter a folder path instead</summary>
      <label>Folder path<input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="/path/to/folder"/></label>
      <button disabled={!typed.trim() || busy !== null} onClick={() => void useTyped()}>Use this path</button>
    </details>}
    {error && <p className="workspace-settings-error" role="status">{error}</p>}
  </div>;
}
