import { useEffect, useRef, useState, type RefObject } from 'react';
import { JobsPreferencesSurface, ResultsPreferencesSurface } from '../prefs/PreferencesSurface';
import { Icon } from './icons';

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

  useEffect(() => {
    let active = true;
    void workspacePath('/path').then(
      (value) => { if (active) setPath(value); },
      (reason: unknown) => { if (active) setError(String(reason)); },
    );
    return () => { active = false; };
  }, []);

  const run = async (action: 'open' | 'select') => {
    setBusy(action);
    setError(undefined);
    try {
      setPath(await workspacePath(action === 'open' ? '/open' : '/select', 'POST'));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(undefined);
    }
  };

  return <section className="settings-theme workspace-settings" aria-labelledby="settings-workspace-title" aria-busy={busy !== undefined}>
    <h3 id="settings-workspace-title">Workspace</h3>
    <p className="workspace-settings-path" title={path}>{path ?? (error ? 'Unavailable' : 'Loading…')}</p>
    <div className="settings-theme-options">
      <button disabled={!path || busy !== undefined} onClick={() => void run('open')}>Open folder</button>
      <button disabled={busy !== undefined} onClick={() => void run('select')}>Select folder…</button>
    </div>
    {error && <p className="workspace-settings-error" role="status">{error}</p>}
  </section>;
}

export function SettingsDialog({ open, theme, onThemeChange, onClose }: {
  open: boolean;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = requestAnimationFrame(() => dialog.current?.querySelector<HTMLElement>(focusableSelector)?.focus());
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
  }, [onClose, open]);

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
        <WorkspaceSettings/>
        <ResultsPreferencesSurface expanded/>
        <JobsPreferencesSurface expanded/>
        <p className="viewer-preferences-note">Viewer preferences remain available in the Viewport panel.</p>
      </div>
    </div>
  </div>;
}
