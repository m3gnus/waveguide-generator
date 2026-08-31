import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { resetWorkspaceFolderStore } from '../stores/workspaceFolder';
import { resetDriverLibraryStore } from '../stores/driverLibrary';
import { SettingsDialog, type Theme } from './SettingsDialog';

function Harness() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>('dark');
  return <><button id="open-settings" onClick={() => setOpen(true)}>Settings</button><SettingsDialog open={open} theme={theme} onThemeChange={setTheme} onClose={() => setOpen(false)}/></>;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

describe('SettingsDialog', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear(); preferencesStore.resetForTests(); resetWorkspaceFolderStore(); resetDriverLibraryStore();
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
    expect(section.textContent).toContain('every CAD project’s archive folder');
    expect(section.textContent).toContain('Documents/Waveguide Generator/runs');
    expect(section.textContent).toContain('path shown above is authoritative');
    expect(section.textContent).toContain('not result exports');
    expect(section.textContent).not.toContain('folder beside Waveguide Generator');

    const buttons = [...section.querySelectorAll<HTMLButtonElement>('button')];
    await act(async () => buttons.find((button) => button.textContent === 'Open folder')!.click());
    await act(async () => buttons.find((button) => button.textContent === 'Select folder…')!.click());

    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/open', { method: 'POST' });
    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/select', { method: 'POST' });
    expect(section.textContent).toContain('/chosen/workspace');
  });

  it('does not let the initial path response overwrite a newer folder selection', async () => {
    const initial = deferred<Response>();
    const selected = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input) === '/api/workspace/path') return initial.promise;
      if (String(input) === '/api/workspace/select') return selected.promise;
      return Promise.resolve(new Response('not found', { status: 404 }));
    }));

    await act(async () => { host.querySelector<HTMLButtonElement>('#open-settings')!.click(); });
    const section = host.querySelector<HTMLElement>('[aria-labelledby="settings-workspace-title"]')!;
    const select = [...section.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Select folder…')!;
    await act(async () => {
      select.click();
      await Promise.resolve();
      selected.resolve(new Response(JSON.stringify({ path: '/newer/selection' }), { status: 200 }));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(section.textContent).toContain('/newer/selection');

    await act(async () => {
      initial.resolve(new Response(JSON.stringify({ path: '/stale/initial' }), { status: 200 }));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(section.textContent).toContain('/newer/selection');
    expect(section.textContent).not.toContain('/stale/initial');
  });

  it('offers both CAD applications, defaulting to Fusion 360', async () => {
    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const select = host.querySelector<HTMLSelectElement>('[aria-label="CAD application"]')!;
    expect(select.value).toBe('fusion360');
    expect(select.options[0].textContent).toBe('Autodesk Fusion 360');
    expect(select.options[1].textContent).toBe('Onshape');
    expect(select.options[1].disabled).toBe(false);
    expect(host.querySelector('#settings-cad')?.textContent).toContain('Choose the WGLink folder');
  });

  it('shows and changes the dedicated WGLink folder without changing the output workspace', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true, path: '/cad/exchange' }));
      if (path === '/api/cad-workspace/open' && init?.method === 'POST') return new Response(JSON.stringify({ selected: true, path: '/cad/exchange' }));
      if (path === '/api/cad-workspace/select' && init?.method === 'POST') return new Response(JSON.stringify({ selected: true, path: '/cad/new-exchange' }));
      if (path === '/api/workspace/path') return new Response(JSON.stringify({ selected: true, path: '/exports' }));
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const cad = host.querySelector<HTMLElement>('#settings-cad')!;
    const wglink = cad.querySelector<HTMLElement>('.cad-setup-folder:not(.cad-project-folder)')!;
    expect(wglink.textContent).toContain('/cad/exchange');
    // The exchange folder is its own setting: the output workspace shown in the
    // project-folder block below must never leak into it.
    expect(wglink.textContent).not.toContain('/exports');
    const buttons = [...wglink.querySelectorAll<HTMLButtonElement>('button')];
    await act(async () => buttons.find((button) => button.textContent === 'Open folder')!.click());
    await act(async () => buttons.find((button) => button.textContent === 'Choose a new folder…')!.click());
    expect(cad.textContent).toContain('/cad/new-exchange');
    expect(fetchMock).toHaveBeenCalledWith('/api/cad-workspace/open', { method: 'POST' });
    expect(fetchMock).toHaveBeenCalledWith('/api/cad-workspace/select', { method: 'POST' });
  });

  it('offers the CAD project folder, and keeps it identical to the workspace one', async () => {
    let stored = '/exports';
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/workspace/path') return new Response(JSON.stringify({ selected: true, path: stored }));
      if (path === '/api/workspace/open' && init?.method === 'POST') return new Response(JSON.stringify({ status: 'opened', path: stored }));
      if (path === '/api/workspace/select' && init?.method === 'POST') {
        stored = init.body ? (JSON.parse(String(init.body)) as { path: string }).path : '/picked/projects';
        return new Response(JSON.stringify({ selected: true, path: stored }));
      }
      if (path === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true, path: '/cad/exchange' }));
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const projects = host.querySelector<HTMLElement>('.cad-project-folder')!;
    const workspace = host.querySelector<HTMLElement>('[aria-labelledby="settings-workspace-title"]')!;
    expect(projects.textContent).toContain('/exports');

    const buttons = [...projects.querySelectorAll<HTMLButtonElement>('.settings-theme-options button')];
    await act(async () => buttons.find((button) => button.textContent === 'Open folder')!.click());
    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/open', { method: 'POST' });

    // The picker runs on the server, exactly as v1's did, so every browser can
    // change the folder rather than only the ones with a directory picker.
    await act(async () => buttons.find((button) => button.textContent === 'Choose a new folder…')!.click());
    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/select', { method: 'POST' });
    expect(projects.textContent).toContain('/picked/projects');
    // One setting: the Workspace section must not still be quoting the old path.
    expect(workspace.textContent).toContain('/picked/projects');
    expect(workspace.textContent).not.toContain('/exports');
  });

  it('accepts a typed project folder for a browser that is not on the server', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/workspace/path') return new Response(JSON.stringify({ selected: false, path: '/exports' }));
      if (path === '/api/workspace/select' && init?.method === 'POST') {
        return new Response(JSON.stringify({ selected: true, path: (JSON.parse(String(init.body)) as { path: string }).path }));
      }
      if (path === '/api/cad-workspace/path') return new Response(JSON.stringify({ selected: true, path: '/cad/exchange' }));
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const projects = host.querySelector<HTMLElement>('.cad-project-folder')!;
    const field = projects.querySelector<HTMLInputElement>('.cad-folder-manual input')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(field, '/typed/projects');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => projects.querySelector<HTMLButtonElement>('.cad-folder-manual button')!.click());

    expect(fetchMock).toHaveBeenCalledWith('/api/workspace/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/typed/projects' }),
    });
    expect(projects.textContent).toContain('/typed/projects');
    // The field empties only because the path was accepted.
    expect(field.value).toBe('');
  });

  it('chooses where the Fusion model is filed and reflects a refused write', async () => {
    let stored = 'run';
    const failNext = { value: false };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') {
        return new Response(JSON.stringify({ selected: true, path: '/cad/exchange', captureMode: stored }));
      }
      if (path === '/api/cad-workspace/capture-document' && init?.method === 'POST') {
        if (failNext.value) return new Response(JSON.stringify({ detail: 'disk is read-only' }), { status: 500 });
        stored = (JSON.parse(String(init.body)) as { mode: string }).mode;
        return new Response(JSON.stringify({ captureDocument: stored !== 'off', captureMode: stored }));
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const cad = host.querySelector<HTMLElement>('#settings-cad')!;
    const choice = (label: string) => [...cad.querySelectorAll<HTMLInputElement>('input[type=radio]')]
      .find((input) => input.closest('label')?.textContent?.includes(label))!;
    // Filed with each run by default: that is where people look for it.
    expect(choice('With every run').checked).toBe(true);

    await act(async () => choice('Once per project').click());
    expect(stored).toBe('project');
    expect(choice('Once per project').checked).toBe(true);

    // A refused write must not leave the radio disagreeing with the file the
    // Fusion add-in reads.
    failNext.value = true;
    await act(async () => choice('Don\u2019t keep one').click());
    expect(choice('Once per project').checked).toBe(true);
    expect(cad.textContent).toContain('disk is read-only');
  });

  it('serializes rapid capture choices so the latest choice is the last server write', async () => {
    const firstWrite = deferred<Response>();
    const secondWrite = deferred<Response>();
    const writes: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/cad-workspace/path') {
        return Promise.resolve(new Response(JSON.stringify({
          selected: true, path: '/cad/exchange', captureMode: 'run',
        })));
      }
      if (path === '/api/cad-workspace/capture-document' && init?.method === 'POST') {
        writes.push((JSON.parse(String(init.body)) as { mode: string }).mode);
        return writes.length === 1 ? firstWrite.promise : secondWrite.promise;
      }
      return Promise.resolve(new Response('not found', { status: 404 }));
    });
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    const cad = host.querySelector<HTMLElement>('#settings-cad')!;
    const choice = (label: string) => [...cad.querySelectorAll<HTMLInputElement>('input[type=radio]')]
      .find((input) => input.closest('label')?.textContent?.includes(label))!;

    await act(async () => {
      choice('Don\u2019t keep one').click();
      choice('With every run').click();
      await Promise.resolve();
    });

    expect(writes).toEqual(['off']);
    await act(async () => {
      firstWrite.resolve(new Response(JSON.stringify({ captureDocument: false, captureMode: 'off' })));
      await firstWrite.promise;
      await Promise.resolve();
    });
    expect(writes).toEqual(['off', 'run']);

    await act(async () => {
      secondWrite.resolve(new Response(JSON.stringify({ captureDocument: true, captureMode: 'run' })));
      await secondWrite.promise;
      await Promise.resolve();
    });
    expect(choice('With every run').checked).toBe(true);
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
    // Scoped to the setup steps: the project-folder block below them legitimately
    // offers a path field, and a section-wide query would stop guarding the keys.
    expect(section.querySelector('.cad-setup-steps input')).toBeNull();
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
    expect(section.textContent).toContain('My account → Developer → API keys');
    expect(section.textContent).toContain('/home/x/.config/hornlab/onshape.env');
    expect(section.querySelector('.cad-setup-steps input')).toBeNull();
  });

  it('separates the rows it indexed from the drivers it can actually offer', async () => {
    // What a catalogue CSV really looks like: thousands of rows indexed, a
    // fraction of them carrying enough T/S to drive anything.
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith('/api/drivers/library')) {
        return new Response(JSON.stringify({
          folder: '/library/driver-databases',
          files: [{ name: 'lf.csv', rows: 1_803 }],
          total_drivers: 1_803,
          complete_drivers: 505,
          last_scan: null,
        }), { status: 200 });
      }
      throw new Error('offline');
    }));
    act(() => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const section = host.querySelector<HTMLElement>('#settings-drivers')!;
    expect(section.querySelector('.driver-library-counts')!.textContent)
      .toBe(`1 file \u00b7 ${(1803).toLocaleString()} drivers indexed \u00b7 505 with Thiele-Small data`);
    // And it says what the difference means, because 1,298 of them will never
    // appear in a search however hard the user looks.
    expect(section.textContent).toContain(`The other ${(1298).toLocaleString()} are catalogue entries`);
  });

  it('counts the shipped library apart from the files the user added', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith('/api/drivers/library')) {
        return new Response(JSON.stringify({
          folder: '/library/driver-databases',
          files: [
            { name: 'hornlab-drivers.csv', rows: 2_162, bundled: true },
            { name: 'mine.csv', rows: 12, bundled: false },
          ],
          total_drivers: 2_170,
          complete_drivers: 651,
          last_scan: null,
        }), { status: 200 });
      }
      throw new Error('offline');
    }));
    act(() => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const section = host.querySelector<HTMLElement>('#settings-drivers')!;
    expect(section.querySelector('.driver-library-counts')!.textContent)
      .toBe(`2 files (1 shipped) \u00b7 ${(2170).toLocaleString()} drivers indexed \u00b7 651 with Thiele-Small data`);
    // The old copy promised an empty application; it no longer is one.
    expect(section.textContent).not.toContain('ships no driver data');
    expect(section.textContent).toContain('ships with Waveguide Generator');
  });

  it('says nothing about catalogue entries when every indexed driver is usable', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith('/api/drivers/library')) {
        return new Response(JSON.stringify({
          folder: '/library/driver-databases',
          files: [{ name: 'lf.csv', rows: 2 }],
          total_drivers: 2,
          complete_drivers: 2,
          last_scan: null,
        }), { status: 200 });
      }
      throw new Error('offline');
    }));
    act(() => host.querySelector<HTMLButtonElement>('#open-settings')!.click());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const section = host.querySelector<HTMLElement>('#settings-drivers')!;
    expect(section.querySelector('.driver-library-counts')!.textContent)
      .toBe('1 file \u00b7 2 drivers indexed \u00b7 2 with Thiele-Small data');
    expect(section.textContent).not.toContain('catalogue entries');
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
