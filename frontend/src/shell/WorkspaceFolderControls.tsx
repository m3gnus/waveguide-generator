import { useEffect, useState } from 'react';
import { useWorkspaceFolderStore } from '../stores/workspaceFolder';
import { Icon } from './icons';

/**
 * The folder WG writes into, wherever it is shown.
 *
 * Selection is the server's native picker (v1's mechanism), never the browser's
 * directory picker: that one exists only in Chromium, and WG is routinely
 * reached from Safari and Firefox. The typed fallback covers the remaining
 * case, a browser on a different machine from the server, where a picker on the
 * server's screen is no help at all.
 */
function useWorkspaceFolder() {
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

/**
 * The same setting, small enough to sit under a project without competing with
 * it. Deliberately not a second copy of the state: both read one store, so
 * changing the folder in Settings retitles this line immediately.
 */
export function ProjectsFolderStrip() {
  const { path, loaded, busy, error, open, select } = useWorkspaceFolder();
  return <div className="cad-projects-folder" aria-busy={busy !== null}>
    <div className="cad-projects-folder-line">
      <span className="cad-detail">Projects folder</span>
      <span className="cad-projects-folder-path" title={path ?? undefined}>{path ?? (loaded ? 'Unavailable' : 'Loading…')}</span>
    </div>
    <div className="cad-projects-folder-actions">
      <button className="link-button" disabled={!path || busy !== null} onClick={() => void open()}><Icon name="folder"/>Open</button>
      <button className="link-button" disabled={busy !== null} onClick={() => void select()}>Change…</button>
    </div>
    {/* Deliberately not the panel's alert channel: a folder that could not be
        read says so quietly, and leaves the loud treatment to the round trip
        this strip merely sits beneath. */}
    {error && <p className="cad-projects-folder-error" role="status">{error}</p>}
  </div>;
}
