import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { getOnshapeConnection, type OnshapeConnection } from '../api/onshape';
import { JobsPreferencesSurface, ResultsPreferencesSurface } from '../prefs/PreferencesSurface';
import { preferencesStore, usePreferences, type CadApplication } from '../prefs/preferences';
import { Icon } from './icons';
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

async function workspacePath(endpoint: '/path' | '/open' | '/select', method?: 'POST'): Promise<string> {
  const response = await fetch(`/api/workspace${endpoint}`, method ? { method } : undefined);
  if (!response.ok) throw new Error(`Workspace request failed (${response.status})`);
  const payload = await response.json() as { path?: unknown };
  if (typeof payload.path !== 'string' || !payload.path) throw new Error('Workspace response has no path');
  return payload.path;
}

function WorkspaceSettings() {
  const [path, setPath] = useState<string>();
  const [busy, setBusy] = useState<'open' | 'select'>();
  const [error, setError] = useState<string>();
  const requestGeneration = useRef(0);

  useEffect(() => {
    const request = ++requestGeneration.current;
    void workspacePath('/path').then(
      (value) => { if (request === requestGeneration.current) setPath(value); },
      (reason: unknown) => { if (request === requestGeneration.current) setError(String(reason)); },
    );
    return () => { requestGeneration.current += 1; };
  }, []);

  const run = async (action: 'open' | 'select') => {
    const request = ++requestGeneration.current;
    setBusy(action);
    setError(undefined);
    try {
      const nextPath = await workspacePath(action === 'open' ? '/open' : '/select', 'POST');
      if (request === requestGeneration.current) setPath(nextPath);
    } catch (reason) {
      if (request === requestGeneration.current) setError(String(reason));
    } finally {
      if (request === requestGeneration.current) setBusy(undefined);
    }
  };

  return <section className="settings-theme workspace-settings" aria-labelledby="settings-workspace-title" aria-busy={busy !== undefined}>
    <h3 id="settings-workspace-title">Workspace</h3>
    <p className="cad-settings-note">Automatic results are saved here. The default is the <code>output</code> folder beside Waveguide Generator; AppData continues to hold internal databases and logs, not new automatic exports.</p>
    <p className="workspace-settings-path" title={path}>{path ?? (error ? 'Unavailable' : 'Loading…')}</p>
    <div className="settings-theme-options">
      <button disabled={!path || busy !== undefined} onClick={() => void run('open')}>Open folder</button>
      <button disabled={busy !== undefined} onClick={() => void run('select')}>Select folder…</button>
    </div>
    {error && <p className="workspace-settings-error" role="status">{error}</p>}
  </section>;
}

/** Report who the stored Onshape key pair authenticates as.
 *
 * Deliberately read-only. The key pair is created by the account owner at
 * Onshape's developer portal and pasted into a file outside every git
 * repository; WG shows where that file is and never offers a field to type a
 * secret into (CAD-LINK-PLAN.md section 8.6).
 */
function OnshapeConnectionStatus() {
  const [connection, setConnection] = useState<OnshapeConnection | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async (refresh: boolean) => {
    setChecking(true); setError(null);
    try {
      setConnection(await getOnshapeConnection(refresh));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setChecking(false); }
  }, []);
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
          No API key pair yet. Create one at <b>dev-portal.onshape.com/keys</b> and save the two values to <code>{connection.credentialsPath}</code> as
          {' '}<code>ONSHAPE_ACCESS_KEY</code> and <code>ONSHAPE_SECRET_KEY</code>. WG reads that file; it never asks you to type a key here.
        </p>}
    {connection.insecureKeyFile && <p className="workspace-settings-error" role="status">
      That key file is readable by other accounts on this machine. Restrict it with <code>chmod 600</code>.
    </p>}
    <div className="settings-theme-options">
      <button disabled={checking} onClick={() => void check(true)}>{checking ? 'Checking…' : 'Check connection'}</button>
    </div>
  </div>;
}

function CadSettings() {
  const preferences = usePreferences();
  const onshape = preferences.cadApplication === 'onshape';
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
      ? 'WG uploads the waveguide to Onshape over its API: the solid arrives as an imported part and the managed WG parameters as a Variable Studio. Sending again replaces the part in place, so features you build on it are kept. Bringing Onshape geometry back into WG for simulation is Fusion-only for now.'
      : 'WGLink opens and updates the active waveguide in Fusion 360, and can bring Fusion geometry back into WG for simulation.'}</p>
    {onshape && <OnshapeConnectionStatus/>}
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
        <WorkspaceSettings/>
        <ResultsPreferencesSurface expanded/>
        <JobsPreferencesSurface expanded/>
        <p className="viewer-preferences-note">Viewer preferences remain available in the Viewport panel.</p>
      </div>
    </div>
  </div>;
}
