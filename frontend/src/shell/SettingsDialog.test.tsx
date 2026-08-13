import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { SettingsDialog, type Theme } from './SettingsDialog';

function Harness() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>('dark');
  return <><button id="open-settings" onClick={() => setOpen(true)}>Settings</button><SettingsDialog open={open} theme={theme} onThemeChange={setTheme} onClose={() => setOpen(false)}/></>;
}

describe('SettingsDialog', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear(); preferencesStore.resetForTests();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    act(() => root.render(<Harness/>));
  });
  afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); host.remove(); });

  it('traps focus, closes on Escape, and restores focus to its opener', () => {
    const opener = host.querySelector<HTMLButtonElement>('#open-settings')!;
    opener.focus();
    act(() => opener.click());
    const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    const focusable = [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled])')];
    const first = focusable[0];
    const last = focusable.at(-1)!;
    last.focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => document.dispatchEvent(tab));
    expect(tab.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('shows the selected workspace and exposes open and select actions', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/workspace/path') {
        return new Response(JSON.stringify({ path: '/data/workspace' }), { status: 200 });
      }
      if (path === '/api/workspace/open' && init?.method === 'POST') {
        return new Response(JSON.stringify({ status: 'opened', path: '/data/workspace' }), { status: 200 });
      }
      if (path === '/api/workspace/select' && init?.method === 'POST') {
        return new Response(JSON.stringify({ selected: true, path: '/chosen/workspace' }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => {
      host.querySelector<HTMLButtonElement>('#open-settings')!.click();
    });

    const section = host.querySelector<HTMLElement>('[aria-labelledby="settings-workspace-title"]')!;
    expect(section).not.toBeNull();
    expect(section.textContent).toContain('/data/workspace');

    const buttons = [...section.querySelectorAll<HTMLButtonElement>('button')];
    await act(async () => buttons.find((button) => button.textContent === 'Open folder')!.click());
    await act(async () => buttons.find((button) => button.textContent === 'Select folder…')!.click());

    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/open', { method: 'POST' });
    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/select', { method: 'POST' });
    expect(section.textContent).toContain('/chosen/workspace');
  });

  it('offers both CAD applications, defaulting to Fusion 360', async () => {
    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const select = host.querySelector<HTMLSelectElement>('[aria-label="CAD application"]')!;
    expect(select.value).toBe('fusion360');
    expect(select.options[0].textContent).toBe('Autodesk Fusion 360');
    expect(select.options[1].textContent).toBe('Onshape');
    expect(select.options[1].disabled).toBe(false);
  });

  /** Open Settings with the CAD application switched to Onshape, serving one
   * canned connection reply. Returns the CAD section for assertions. */
  async function openOnshapeSettings(connection: Record<string, unknown>): Promise<HTMLElement> {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith('/api/cadlink/onshape/connection')) {
        return new Response(JSON.stringify(connection), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    }));
    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const select = host.querySelector<HTMLSelectElement>('[aria-label="CAD application"]')!;
    await act(async () => {
      select.value = 'onshape';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    return host.querySelector<HTMLElement>('#settings-cad')!;
  }

  it('reports the Onshape account and its plan, and never offers a field to type a key into', async () => {
    const section = await openOnshapeSettings({
      configured: true,
      reachable: true,
      credentialsPath: '/home/x/.config/hornlab/onshape.env',
      detail: null,
      insecureKeyFile: false,
      account: { id: 'ACC', name: 'Test Owner' },
      plan: { name: 'Onshape Free public only', group: 'Free', publicOnly: true },
    });
    expect(section.textContent).toContain('Test Owner');
    // The free-plan consequence must be stated before anything is sent.
    expect(section.textContent).toContain('every document public');
    // The key pair is pasted into a file by its owner, never typed into WG.
    expect(section.querySelector('input')).toBeNull();
  });

  it('says where to put a key pair that is not configured yet', async () => {
    const section = await openOnshapeSettings({
      configured: false,
      reachable: false,
      credentialsPath: '/home/x/.config/hornlab/onshape.env',
      detail: 'No Onshape API key pair was found.',
      insecureKeyFile: false,
      account: null,
      plan: null,
    });
    expect(section.textContent).toContain('dev-portal.onshape.com/keys');
    expect(section.textContent).toContain('/home/x/.config/hornlab/onshape.env');
    expect(section.querySelector('input')).toBeNull();
  });

  it('warns when the Onshape key file is readable by other accounts', async () => {
    const section = await openOnshapeSettings({
      configured: true,
      reachable: true,
      credentialsPath: '/home/x/.config/hornlab/onshape.env',
      detail: null,
      insecureKeyFile: true,
      account: { id: 'ACC', name: 'Test Owner' },
      plan: { name: 'Professional', group: 'Professional', publicOnly: false },
    });
    expect(section.textContent).toContain('chmod 600');
    expect(section.textContent).not.toContain('every document public');
  });
});
