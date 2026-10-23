import { useEffect, useRef, useState } from 'react';
import { getFolderRoot, openFolderRoot, selectFolderRoot, useRootFolder, type FolderRoot } from '../api/folders';
import { openCadWorkspace } from '../api/cadWorkspace';
import { cadWorkspaceSelection } from '../stores/cadWorkspaceSelection';

/** One chooser and one explicit override action in Settings, setup and recovery. */
export function RootFolderControls({ cadlinkOnly = false, showPath = true }: { cadlinkOnly?: boolean; showPath?: boolean }) {
  const generation = useRef(0);
  const changing = useRef(false);
  const [folder, setFolder] = useState<FolderRoot>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [typed, setTyped] = useState('');
  useEffect(() => {
    const load = () => {
      if (changing.current) return;
      const request = ++generation.current;
      void getFolderRoot().then(value => { if (request === generation.current) setFolder(value); }, reason => { if (request === generation.current) setError(String(reason)); });
    };
    load();
    const unsubscribe = cadWorkspaceSelection.subscribe(load);
    return () => { unsubscribe(); generation.current += 1; };
  }, []);
  const run = async (action: 'open' | 'select' | 'cadlink' | 'runs', path?: string) => {
    const destination = action === 'cadlink' || action === 'runs' ? folder?.rootChildren?.[action] ?? `${folder?.path}/${action}` : '';
    if ((action === 'cadlink' || action === 'runs') && folder?.overrides?.[action]) {
      if (!window.confirm(`Use the WG folder instead? No files will move. New ${action === 'cadlink' ? 'WGLink' : 'Runs'} files will go to ${destination}.`)) return;
    }
    const request = ++generation.current;
    changing.current = true;
    setBusy(true); setError(undefined);
    try {
      if (action === 'open' && cadlinkOnly) { await openCadWorkspace(); return; }
      const value = action === 'open' ? await openFolderRoot()
        : action === 'select' ? await selectFolderRoot(path) : await useRootFolder(action, destination, folder?.[action] ?? '');
      if (request !== generation.current) return;
      setFolder(value);
      if (action !== 'open' && value.selected) {
        setTyped('');
      }
    } catch (reason) {
      if (request === generation.current) setError(reason instanceof Error ? reason.message : String(reason));
      const current = await getFolderRoot().catch(() => undefined);
      if (current && request === generation.current) setFolder(current);
    }
    finally { changing.current = false; if (request === generation.current) setBusy(false); }
  };
  const missing = cadlinkOnly && folder?.cadlinkExists === false;
  const names = cadlinkOnly ? ['cadlink'] as const : ['cadlink', 'runs'] as const;
  return <div aria-busy={busy}>
    {cadlinkOnly
      ? showPath && <p className="workspace-settings-path">{missing ? `WGLink folder not found: ${folder.cadlink}` : folder?.cadlink ?? 'WGLink folder unavailable'}</p>
      : <>
        <p className="workspace-settings-path" title={folder?.path ?? undefined}>{folder?.path ?? 'Loading…'}</p>
        <p className="cad-settings-note">WG saves the WGLink folder in <code>cadlink/</code> and the Runs folder in <code>runs/</code>. Changing the WG folder moves no files. One-off exports can go elsewhere.</p>
      </>}
    {folder && <dl>
      {names.map(name => <div key={name}>
        {!cadlinkOnly && <dt>{name === 'cadlink' ? 'WGLink folder' : 'Runs folder'}</dt>}
        {(!cadlinkOnly || folder.overrides?.[name]) && <dd>
          {folder.overrides?.[name] ? <><small>Separate folder from an earlier setup: {folder[name]}</small>{' '}
            <button disabled={busy || folder.readOnly} onClick={() => void run(name)}>Use the WG folder instead</button></> : folder[name]}
        </dd>}
      </div>)}
    </dl>}
    {folder?.rootNote && <p role="status">{folder.rootNote}</p>}
    {folder?.notice && <p role="status">{folder.notice}</p>}
    <div className="settings-theme-options">
      {missing && !folder?.overrides?.cadlink && <button disabled={busy || folder?.readOnly} onClick={() => void run('cadlink')}>Create it</button>}
      <button disabled={busy || (cadlinkOnly ? !folder?.cadlink || missing : !folder?.path)} onClick={() => void run('open')}>{cadlinkOnly ? 'Open WGLink folder' : 'Open WG folder'}</button>
      <button disabled={busy || folder?.readOnly} onClick={() => void run('select')}>Choose WG folder…</button>
    </div>
    <details className="cad-folder-manual">
      <summary>Enter a folder path instead</summary>
      <label>WG folder path<input value={typed} onChange={event => setTyped(event.target.value)}/></label>
      <button disabled={busy || !typed.trim() || folder?.readOnly} onClick={() => void run('select', typed.trim())}>Use this path</button>
    </details>
    {error && <p role="status" className="workspace-settings-error">{error}</p>}
  </div>;
}
