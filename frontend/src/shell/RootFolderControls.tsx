import { useEffect, useRef, useState } from 'react';
import { getFolderRoot, openFolderRoot, selectFolderRoot, type FolderRoot } from '../api/folders';
import { resetWorkspaceFolderStore, useWorkspaceFolderStore } from '../stores/workspaceFolder';
import { cadWorkspaceSelection } from '../stores/cadWorkspaceSelection';

export function RootFolderControls() {
  const generation = useRef(0);
  const [folder, setFolder] = useState<FolderRoot>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [typed, setTyped] = useState('');
  useEffect(() => {
    const request = ++generation.current;
    void getFolderRoot().then(value => { if (request === generation.current) setFolder(value); }, reason => { if (request === generation.current) setError(String(reason)); });
    return () => { generation.current += 1; };
  }, []);
  const run = async (action: 'open' | 'select', path?: string) => {
    const request = ++generation.current;
    setBusy(true); setError(undefined);
    try {
      const value = action === 'open' ? await openFolderRoot() : await selectFolderRoot(path);
      if (request !== generation.current) return;
      setFolder(value);
      if (action === 'select' && value.selected) {
        setTyped('');
        resetWorkspaceFolderStore();
        cadWorkspaceSelection.noteSelection(true);
        void useWorkspaceFolderStore.getState().load();
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };
  return <div aria-busy={busy}>
    <p className="workspace-settings-path" title={folder?.path ?? undefined}>{folder?.path ?? 'Loading…'}</p>
    <p className="cad-settings-note">WG saves the WGLink folder in <code>cadlink/</code> and the Runs folder in <code>runs/</code>. Changing the WG root folder moves no files. One-off exports can go elsewhere.</p>
    {folder && <dl>
      {(['cadlink', 'runs'] as const).map(name => <div key={name}>
        <dt>{name === 'cadlink' ? 'WGLink folder' : 'Runs folder'}</dt>
        <dd>{folder[name]}{folder.overrides?.[name] && <small> — kept from an earlier setup</small>}</dd>
      </div>)}
    </dl>}
    {folder?.notice && <p role="status">{folder.notice}</p>}
    <div className="settings-theme-options">
      <button disabled={busy || !folder?.path} onClick={() => void run('open')}>Open WG root folder</button>
      <button disabled={busy || folder?.readOnly} onClick={() => void run('select')}>Choose WG root folder…</button>
    </div>
    <details className="cad-folder-manual">
      <summary>Enter a folder path instead</summary>
      <label>WG root folder path<input value={typed} onChange={event => setTyped(event.target.value)}/></label>
      <button disabled={busy || !typed.trim() || folder?.readOnly} onClick={() => void run('select', typed.trim())}>Use this path</button>
    </details>
    {error && <p role="status" className="workspace-settings-error">{error}</p>}
  </div>;
}
