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
    expect(section.textContent).toContain('not result exports');

    const buttons = [...section.querySelectorAll<HTMLButtonElement>('button')];
    await act(async () => buttons.find((button) => button.textContent === 'Open folder')!.click());
    await act(async () => buttons.find((button) => button.textContent === 'Select folder…')!.click());

    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/open', { method: 'POST' });
    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/select', { method: 'POST' });
    expect(section.textContent).toContain('/chosen/workspace');
  });

  it('names Fusion 360 as the CAD application and labels Onshape as coming soon', async () => {
    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const select = host.querySelector<HTMLSelectElement>('[aria-label="CAD application"]')!;
    expect(select.value).toBe('fusion360');
    expect(select.options[0].textContent).toBe('Autodesk Fusion 360');
    expect(select.options[1].textContent).toContain('coming soon');
    expect(select.options[1].disabled).toBe(true);
  });
});
