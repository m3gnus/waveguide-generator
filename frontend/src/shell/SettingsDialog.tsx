import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { getOnshapeConnection, type OnshapeConnection } from '../api/onshape';
import {
  getCadWorkspace,
  openCadWorkspace,
  selectCadWorkspace,
  setCaptureMode,
  type CadCaptureMode,
} from '../api/cadWorkspace';
import { JobsPreferencesSurface, ResultsPreferencesSurface } from '../prefs/PreferencesSurface';
import { preferencesStore, usePreferences, type CadApplication } from '../prefs/preferences';
import { useDriverLibraryStore } from '../stores/driverLibrary';
import { Icon } from './icons';
import { WorkspaceFolderControls } from './WorkspaceFolderControls';
import type { SettingsSection } from './settingsNavigation';

export type Theme = 'dark' | 'light';

const focusableSelector = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function trapDialogFocus(dialog: RefObject<HTMLElement | null>, event: KeyboardEvent): void {
  if (event.key !== 'Tab') return;
  const focusable = [...(dialog.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])]
    .filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1)!;
  if (event.shiftKey && (document.activeElement === first || !dialog.current?.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * Where the driver library reads its CSV files, and how many it found.
 *
 * Read-only apart from *Rescan*: the folder is resolved per platform beside
 * WG's other application data, and the index rebuilds itself whenever a file's
 * mtime changes, so this button is for the case where someone wants to see the
 * count move after dropping a file in.
 */
function DriverLibrarySettings() {
  const status = useDriverLibraryStore((store) => store.status);
  const info = useDriverLibraryStore((store) => store.info);
  const error = useDriverLibraryStore((store) => store.error);
  const load = useDriverLibraryStore((store) => store.load);
  const rescan = useDriverLibraryStore((store) => store.rescan);

  useEffect(() => { void load(); }, [load]);

  const files = info?.files.length ?? 0;
  return <section id="settings-drivers" className="settings-theme driver-library-settings" aria-labelledby="settings-drivers-title" tabIndex={-1}>
    <h3 id="settings-drivers-title">Driver library</h3>
    <p className={`workspace-settings-path ${info?.folder ? '' : 'not-selected'}`} title={info?.folder ?? undefined}>{info?.folder ?? 'Not resolved yet'}</p>
    <p className="cad-settings-note">Waveguide Generator ships no driver data. Put CSV files in this folder and their drivers become searchable in <b>Drivers</b>.</p>
    <div className="driver-library-counts">
      <span>{files.toLocaleString()} file{files === 1 ? '' : 's'} · {(info?.total_drivers ?? 0).toLocaleString()} driver{info?.total_drivers === 1 ? '' : 's'}</span>
    </div>
    <div className="settings-theme-options">
      <button disabled={status === 'loading'} onClick={() => void rescan()}>{status === 'loading' ? 'Rescanning…' : 'Rescan'}</button>
    </div>
    {error && <p className="workspace-settings-error" role="status">{error}</p>}
  </section>;
}

function WorkspaceSettings() {
  return <section className="settings-theme workspace-settings" aria-labelledby="settings-workspace-title">
    <h3 id="settings-workspace-title">Workspace</h3>
    <WorkspaceFolderControls note={<>Manual and automatic run exports are saved here, and so is every CAD project’s archive folder. The default is the <code>output</code> folder beside Waveguide Generator; AppData continues to hold internal databases and logs, not result exports.</>}/>
  </section>;
}

/** The three places a returned Fusion model can end up, in plain terms. */
const CAPTURE_CHOICES: Array<{ mode: CadCaptureMode; label: string; detail: string }> = [
  {
    mode: 'run',
    label: 'With every run',
    detail: 'In each run folder, beside the results it produced. Easiest to find; one copy per solve.',
  },
  {
    mode: 'project',
    label: 'Once per project',
    detail: 'In runs/<project>/cad/, only the newest model state -- archiving a later one deletes the last. Saves space when sweeping one geometry; each run folder keeps its own copy regardless.',
  },
  {
    mode: 'off',
    label: 'Don\u2019t keep one',
    detail: 'Returns carry no model. Older runs cannot be reopened in Fusion from WG.',
  },
];

function CadFolderSettings() {
  const [path, setPath] = useState<string | null>();
  const [capture, setCapture] = useState<CadCaptureMode>('run');
  const [busy, setBusy] = useState<'open' | 'select'>();
  const [error, setError] = useState<string>();
  const [manualPath, setManualPath] = useState('');
  const requestGeneration = useRef(0);

  useEffect(() => {
    const request = ++requestGeneration.current;
    void getCadWorkspace().then(
      (value) => {
        if (request !== requestGeneration.current) return;
        setPath(value.path);
        setCapture(value.captureMode ?? (value.captureDocument === false ? 'off' : 'run'));
      },
      (reason: unknown) => { if (request === requestGeneration.current) setError(String(reason)); },
    );
    return () => { requestGeneration.current += 1; };
  }, []);

  const run = async (action: 'open' | 'select', requestedPath?: string) => {
    const request = ++requestGeneration.current;
    setBusy(action); setError(undefined);
    try {
      const result = action === 'open' ? await openCadWorkspace() : await selectCadWorkspace(requestedPath);
      if (request === requestGeneration.current) {
        setPath(result.path);
        if (requestedPath && result.selected) setManualPath('');
      }
    } catch (reason) {
      if (request === requestGeneration.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (request === requestGeneration.current) setBusy(undefined);
    }
  };

  const chooseCapture = async (mode: CadCaptureMode) => {
    // Optimistic: the chosen option is the state, and a refused write puts the
    // previous one back rather than leaving the radio disagreeing with the file
    // the add-in reads.
    const previous = capture;
    setCapture(mode); setError(undefined);
    try {
      setCapture(await setCaptureMode(mode));
    } catch (reason) {
      setCapture(previous);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return <div className="cad-setup-folder" aria-busy={busy !== undefined}>
    <p className={`workspace-settings-path ${path ? '' : 'not-selected'}`} title={path ?? undefined}>{path ?? 'No WGLink folder selected'}</p>
    <p className="cad-settings-note">WG creates <code>wglink</code> and <code>wgreturn</code> inside this folder. Choose a stable local folder that both WG and the Fusion add-in can access.</p>
    <div className="settings-theme-options">
      <button disabled={!path || busy !== undefined} onClick={() => void run('open')}>Open folder</button>
      <button disabled={busy !== undefined} onClick={() => void run('select')}>{path ? 'Choose a new folder…' : 'Choose WGLink folder…'}</button>
    </div>
    <details className="cad-folder-manual">
      <summary>Enter a folder path instead</summary>
      <label>WGLink folder path<input value={manualPath} onChange={(event) => setManualPath(event.target.value)} placeholder="/path/to/WGLink exchange"/></label>
      <button disabled={!manualPath.trim() || busy !== undefined} onClick={() => void run('select', manualPath.trim())}>Use this path</button>
    </details>
    <fieldset className="cad-settings-capture">
      <legend>Keep a copy of the Fusion model</legend>
      {CAPTURE_CHOICES.map(({ mode, label, detail }) => <label key={mode} className="ui-check">
        <input
          type="radio"
          name="cad-capture-mode"
          value={mode}
          checked={capture === mode}
          onChange={() => void chooseCapture(mode)}
        />
        <span><b>{label}</b><small>{detail}</small></span>
      </label>)}
    </fieldset>
    {error && <p className="workspace-settings-error" role="status">{error}</p>}
  </div>;
}

/** Report who the stored Onshape key pair authenticates as.
 *
 * Deliberately read-only. The key pair is created by the account owner at
 * Onshape's developer portal and pasted into a file outside every git
 * repository; WG shows where that file is and never offers a field to type a
 * secret into (CAD-LINK-PLAN.md section 8.6).
 */
function OnshapeConnectionStatus({ onConnection }: {
  onConnection?: (connection: OnshapeConnection) => void;
}) {
  const [connection, setConnection] = useState<OnshapeConnection | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async (refresh: boolean) => {
    setChecking(true); setError(null);
    try {
      const next = await getOnshapeConnection(refresh);
      setConnection(next);
      onConnection?.(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setChecking(false); }
  }, [onConnection]);
  useEffect(() => { void check(false); }, [check]);

  if (checking && connection === null) return <p className="cad-settings-note">Checking the Onshape connection…</p>;
  if (error) return <p className="workspace-settings-error" role="status">{error}</p>;
  if (!connection) return null;

  return <div className="cad-settings-connection">
    {connection.configured
      ? connection.reachable
        ? <p className="cad-settings-note" role="status">
            Connected as <b>{connection.account?.name ?? 'this account'}</b>
            {connection.plan?.name ? ` · ${connection.plan.name}` : ''}
            {connection.plan?.publicOnly === true
              ? ' — this plan makes every document public, so anyone with the link can view designs you send.'
              : ''}
          </p>
        : <p className="workspace-settings-error" role="status">{connection.detail ?? 'Onshape could not be reached with the stored key pair.'}</p>
      : <p className="cad-settings-note">
          No API key pair yet. In Onshape, open <b>My account → Developer → API keys</b>, create one, and save the two values to <code>{connection.credentialsPath}</code> as
          {' '}<code>ONSHAPE_ACCESS_KEY</code> and <code>ONSHAPE_SECRET_KEY</code>. WG reads that file; it never asks you to type a key here.
        </p>}
    {connection.configured && <p className="cad-settings-note">Credential file: <code>{connection.credentialsPath}</code>. Environment variables with the same names take precedence.</p>}
    {connection.insecureKeyFile && <p className="workspace-settings-error" role="status">
      That key file is readable by other accounts on this machine. Restrict it with <code>chmod 600</code>.
    </p>}
    <div className="settings-theme-options">
      <button disabled={checking} onClick={() => void check(true)}>{checking ? 'Checking…' : 'Check connection'}</button>
    </div>
  </div>;
}

/**
 * Where CAD projects are archived -- which is the output workspace.
 *
 * A project's folder is <workspace>/<project>, so this is not a second setting
 * with its own path to drift out of step; it is the workspace setting, offered
 * where someone looking at a project would think to change it. Both surfaces
 * read one store, so a change here is visible in Workspace below without
 * reopening the dialog.
 */
function CadProjectFolderSettings() {
  return <div className="cad-setup-folder cad-project-folder">
    <h4 className="cad-settings-subhead">Project folder</h4>
    <WorkspaceFolderControls
      manual
      selectLabel="Choose a new folder…"
      note={<>Each CAD project keeps its runs and captured models in its own folder here. This is the same folder as <b>Workspace</b> below, and changing it in either place changes both. Existing projects are not moved.</>}
    />
  </div>;
}

function CadSettings() {
  const preferences = usePreferences();
  const onshape = preferences.cadApplication === 'onshape';
  const [onshapeSetup, setOnshapeSetup] = useState<OnshapeConnection | null>(null);
  const rememberOnshapeConnection = useCallback((connection: OnshapeConnection) => setOnshapeSetup(connection), []);
  return <section id="settings-cad" className="settings-theme cad-settings" aria-labelledby="settings-cad-title" tabIndex={-1}>
    <h3 id="settings-cad-title">CAD Link</h3>
    <label className="ui-field">CAD application<select
      aria-label="CAD application"
      value={preferences.cadApplication}
      onChange={(event) => preferencesStore.update({ cadApplication: event.target.value as CadApplication })}
    >
      <option value="fusion360">Autodesk Fusion 360</option>
      <option value="onshape">Onshape</option>
    </select></label>
    <p className="cad-settings-note">{onshape
      ? 'WG connects directly to your Onshape account. No local exchange folder or add-in is needed.'
      : 'Fusion uses the WGLink add-in and one local exchange folder. Complete these steps once; WGLink then opens and updates designs from the CAD Link panel.'}</p>
    {onshape ? <ol className="cad-setup-steps" aria-label="Set up Onshape">
      <li><b>Create an Onshape API key</b><span>In Onshape, open <b>My account → Developer → API keys</b>, create a key for this personal connection, and copy both values before closing the dialog. <a href="https://cad.onshape.com/help/Content/Plans/my_account_developer.htm" target="_blank" rel="noreferrer noopener">Onshape instructions</a></span></li>
      <li><b>Store the key in WG’s private credential file</b><span>Save <code>ONSHAPE_ACCESS_KEY=…</code> and <code>ONSHAPE_SECRET_KEY=…</code> in <code>{onshapeSetup?.credentialsPath ?? 'the credential file shown after the connection check'}</code>. Keep it out of synced or shared folders. WG reads it locally and never returns either value to the browser.</span></li>
      <li><b>Verify the account</b><span>The check reports the account and plan that will own new CAD documents.</span><OnshapeConnectionStatus onConnection={rememberOnshapeConnection}/></li>
    </ol> : <ol className="cad-setup-steps" aria-label="Set up Autodesk Fusion 360">
      <li><b>Install and start WGLink</b><span>Install the WGLink add-in once, restart Fusion, then confirm <b>Utilities → Scripts and Add-Ins → WGLink</b> is set to run on startup. <a href="https://github.com/m3gnus/hornlab-fusion-addin/tree/main/fusion-addins/WGLink#install" target="_blank" rel="noreferrer noopener">WGLink install guide</a></span></li>
      <li><b>Choose the WGLink folder</b><span>This is separate from the output folder below. WG and Fusion read the same setting, so it is chosen only here.</span><CadFolderSettings/></li>
      <li><b>Open a design in Fusion</b><span>Close Settings, open <b>CAD Link</b>, and choose <b>Open in Fusion 360</b>. WG writes the bundle, starts Fusion, and the connection card confirms when WGLink is online.</span></li>
    </ol>}
    <CadProjectFolderSettings/>
  </section>;
}

export function SettingsDialog({ open, theme, focusSection, onThemeChange, onClose }: {
  open: boolean;
  theme: Theme;
  focusSection?: SettingsSection;
  onThemeChange: (theme: Theme) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = requestAnimationFrame(() => {
      const section = focusSection ? dialog.current?.querySelector<HTMLElement>(`#settings-${focusSection}`) : null;
      section?.scrollIntoView({ block: 'start' });
      (section?.querySelector<HTMLElement>('select:not([disabled]), button:not([disabled])')
        ?? dialog.current?.querySelector<HTMLElement>(focusableSelector))?.focus();
    });
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      trapDialogFocus(dialog, event);
    };
    document.addEventListener('keydown', keydown);
    return () => {
      cancelAnimationFrame(focus);
      document.removeEventListener('keydown', keydown);
      previous?.focus();
    };
  }, [focusSection, onClose, open]);

  if (!open) return null;
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialog} className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header><div><h2 id="settings-title">Settings</h2><p>Application preferences are saved automatically.</p></div><button className="dialog-close" aria-label="Close settings" onClick={onClose}><Icon name="close"/></button></header>
      <div className="settings-scroll">
        <section className="settings-theme" aria-labelledby="settings-theme-title">
          <h3 id="settings-theme-title">Theme</h3>
          <div className="settings-theme-options">
            <button className={theme === 'dark' ? 'on' : ''} aria-pressed={theme === 'dark'} onClick={() => onThemeChange('dark')}><Icon name="moon"/>Dark</button>
            <button className={theme === 'light' ? 'on' : ''} aria-pressed={theme === 'light'} onClick={() => onThemeChange('light')}><Icon name="sun"/>Light</button>
          </div>
        </section>
        <CadSettings/>
        <DriverLibrarySettings/>
        <WorkspaceSettings/>
        <ResultsPreferencesSurface expanded/>
        <JobsPreferencesSurface expanded/>
        <p className="viewer-preferences-note">Viewer preferences remain available in the Viewport panel.</p>
      </div>
    </div>
  </div>;
}
