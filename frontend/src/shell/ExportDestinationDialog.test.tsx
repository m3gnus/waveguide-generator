import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExportDestinationDialog } from './ExportDestinationDialog';
import {
  askForExportDestination,
  askToReplaceExports,
  type ExportDestinationChoice,
} from './exportDestinationPrompt';

/**
 * The dialog every manual export goes through.
 *
 * What matters here is not the markup: it is that an export cannot start
 * without an answer, that cancelling answers "nothing", and that answering it
 * never touches the workspace setting the rest of the application writes into.
 */
describe('the export destination dialog', () => {
  let host: HTMLDivElement;
  let root: Root;
  let calls: Array<{ path: string; init?: RequestInit }>;

  const respond = (payload: Record<string, unknown>) => new Response(
    JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } },
  );

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    calls = [];
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function stubFetch(handler: (path: string, init?: RequestInit) => Response) {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push({ path, init });
      return handler(path, init);
    }));
  }

  async function ask(): Promise<{ answer: Promise<ExportDestinationChoice | null> }> {
    await act(async () => { root.render(<ExportDestinationDialog/>); });
    let answer!: Promise<ExportDestinationChoice | null>;
    await act(async () => {
      answer = askForExportDestination({ title: 'Export STEP', detail: 'horn.step.' });
      await Promise.resolve();
    });
    return { answer };
  }

  function button(label: string): HTMLButtonElement {
    const found = [...document.querySelectorAll<HTMLButtonElement>('.export-dialog button')]
      .find((element) => element.textContent?.trim() === label);
    if (!found) throw new Error(`Missing button: ${label}`);
    return found;
  }

  it('offers the folder the last export used, and exports there on one click', async () => {
    stubFetch(() => respond({
      path: '/exports', token: 'handle-1', remembered: true, selected: false,
    }));
    const { answer } = await ask();

    expect(document.querySelector('.export-destination-path')?.textContent)
      .toBe('/exports');
    expect(document.querySelector('.export-dialog')?.textContent)
      .toContain('Where your last export went.');
    await act(async () => { button('Export here').click(); });

    await expect(answer).resolves.toEqual({
      token: 'handle-1', directory: '/exports',
    });
    // Reading the suggestion is one GET. Nothing was chosen, so nothing was
    // posted, and the workspace endpoints were never touched.
    expect(calls.map(({ path }) => path)).toEqual(['/api/workspace/export-destination']);
  });

  it('says the folder is the output folder until an export has moved it', async () => {
    stubFetch(() => respond({
      path: '/workspace', token: 'handle-workspace', remembered: false, selected: false,
    }));
    await ask();

    expect(document.querySelector('.export-dialog')?.textContent)
      .toContain('WG’s output folder, until an export goes somewhere else.');
  });

  it('opens the platform picker on request and offers what it returned', async () => {
    stubFetch((_path, init) => respond(init?.method === 'POST'
      ? { path: '/Volumes/Stick', token: 'handle-2', remembered: false, selected: true }
      : { path: '/workspace', token: 'handle-1', remembered: false, selected: false }));
    const { answer } = await ask();

    await act(async () => { button('Choose folder…').click(); });
    expect(document.querySelector('.export-destination-path')?.textContent).toBe('/Volumes/Stick');
    await act(async () => { button('Export here').click(); });

    await expect(answer).resolves.toEqual({ token: 'handle-2', directory: '/Volumes/Stick' });
    const chose = calls.find(({ init }) => init?.method === 'POST')!;
    expect(chose.path).toBe('/api/workspace/export-destination');
    // No body: the server opens its own picker. A path here would mean the
    // browser had chosen the folder, which it cannot.
    expect(chose.init?.body).toBeUndefined();
    // Never the workspace route: choosing where one export goes must not move
    // the folder runs, archives and CAD projects live in.
    expect(calls.some(({ path }) => path.includes('/api/workspace/select'))).toBe(false);
  });

  it('accepts a typed path, for WG reached from another machine', async () => {
    stubFetch((_path, init) => respond(init?.method === 'POST'
      ? { path: '/srv/exports', token: 'handle-typed', remembered: false, selected: true }
      : { path: '/workspace', token: 'handle-1', remembered: false, selected: false }));
    const { answer } = await ask();

    const field = document.querySelector<HTMLInputElement>('.cad-folder-manual input')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(field, '/srv/exports');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => { button('Use this path').click(); });
    await act(async () => { button('Export here').click(); });

    await expect(answer).resolves.toEqual({ token: 'handle-typed', directory: '/srv/exports' });
    const chose = calls.find(({ init }) => init?.method === 'POST')!;
    expect(JSON.parse(String(chose.init?.body))).toEqual({ path: '/srv/exports' });
  });

  it('answers nothing when the export is cancelled', async () => {
    stubFetch(() => respond({
      path: '/workspace', token: 'handle-1', remembered: false, selected: false,
    }));
    const { answer } = await ask();

    await act(async () => { button('Cancel').click(); });

    await expect(answer).resolves.toBeNull();
    expect(document.querySelector('.export-dialog')).toBeNull();
  });

  it('keeps the standing suggestion when the platform picker is cancelled', async () => {
    stubFetch((_path, init) => respond(init?.method === 'POST'
      // What the server answers a cancelled picker with: the same offer, not a
      // new folder and not an error.
      ? { path: '/workspace', token: 'handle-2', remembered: false, selected: false }
      : { path: '/workspace', token: 'handle-1', remembered: false, selected: false }));
    const { answer } = await ask();

    await act(async () => { button('Choose folder…').click(); });

    expect(document.querySelector('.export-dialog')).not.toBeNull();
    expect(document.querySelector('.export-destination-path')?.textContent)
      .toBe('/workspace');
    await act(async () => { button('Cancel').click(); });
    await expect(answer).resolves.toBeNull();
  });

  it('cannot export when no folder could be offered', async () => {
    stubFetch(() => respond({ path: null, token: null, remembered: false, selected: false }));
    await ask();

    expect(button('Export here').disabled).toBe(true);
    expect(document.querySelector('.export-destination-path')?.textContent)
      .toBe('No folder available');
  });

  it('reports a refused folder and stays open', async () => {
    stubFetch((_path, init) => (init?.method === 'POST'
      ? new Response(JSON.stringify({ detail: 'Selected path is not a directory: /nope' }), { status: 400 })
      : respond({ path: '/workspace', token: 'handle-1', remembered: false, selected: false })));
    await ask();

    await act(async () => { button('Choose folder…').click(); });

    expect(document.querySelector('.workspace-settings-error')?.textContent)
      .toContain('not a directory');
    expect(document.querySelector('.export-dialog')).not.toBeNull();
  });

  it('answers a second export "cancelled" rather than losing the question on screen', async () => {
    stubFetch(() => respond({
      path: '/workspace', token: 'handle-1', remembered: false, selected: false,
    }));
    const { answer } = await ask();

    let second!: Promise<ExportDestinationChoice | null>;
    await act(async () => {
      second = askForExportDestination({ title: 'Export STL' });
      await Promise.resolve();
    });

    await expect(second).resolves.toBeNull();
    expect(document.querySelector('#export-destination-title')?.textContent).toBe('Export STEP');
    await act(async () => { button('Cancel').click(); });
    await expect(answer).resolves.toBeNull();
  });

  it('refuses to export when no dialog is mounted, rather than writing somewhere unasked', async () => {
    await expect(askForExportDestination({ title: 'Export STEP' })).rejects
      .toThrow('not available');
  });

  it('keeps the folder just chosen when a second picker is cancelled', async () => {
    // Reaching for the picker again and thinking better of it is not a request
    // to go back to the folder the last export used.
    let answer: Record<string, unknown> = {
      path: '/Volumes/Stick', token: 'handle-2', remembered: false, selected: true,
    };
    stubFetch((_path, init) => respond(init?.method === 'POST'
      ? answer
      : { path: '/workspace', token: 'handle-1', remembered: true, selected: false }));
    const { answer: chosen } = await ask();

    await act(async () => { button('Choose folder…').click(); });
    expect(document.querySelector('.export-destination-path')?.textContent).toBe('/Volumes/Stick');

    // What the server answers a cancelled picker with: no handle, and the
    // standing suggestion rather than the folder on screen.
    answer = { path: '/workspace', token: null, remembered: true, selected: false };
    await act(async () => { button('Choose folder…').click(); });

    expect(document.querySelector('.export-destination-path')?.textContent).toBe('/Volumes/Stick');
    await act(async () => { button('Export here').click(); });
    await expect(chosen).resolves.toEqual({ token: 'handle-2', directory: '/Volumes/Stick' });
  });

  it('settles a question left open when the dialog unmounts', async () => {
    // Otherwise the export that asked awaits a promise nobody can settle, and
    // its panel sits busy for the rest of the session.
    stubFetch(() => respond({
      path: '/workspace', token: 'handle-1', remembered: false, selected: false,
    }));
    const { answer } = await ask();
    const replace = askToReplaceExports({ directory: '/x', paths: ['/x/a.csv'] });

    await act(async () => { root.unmount(); });

    await expect(answer).resolves.toBeNull();
    // The second question was refused while the first was on screen; either way
    // an unanswerable "replace these?" means no.
    await expect(replace).resolves.toBe(false);
    root = createRoot(host);
  });

  describe('the replace question', () => {
    // Wrapped, never returned bare: awaiting an async function that returns a
    // promise awaits that promise too, and this one is the answer under test.
    async function askReplace(paths: string[]): Promise<{ answer: Promise<boolean> }> {
      await act(async () => { root.render(<ExportDestinationDialog/>); });
      let answer!: Promise<boolean>;
      await act(async () => {
        answer = askToReplaceExports({ directory: '/exports', paths });
        await Promise.resolve();
      });
      return { answer };
    }

    it('names the files it would replace and writes only when told to', async () => {
      const { answer } = await askReplace([
        '/exports/horn.csv',
        '/exports/horn.json',
      ]);

      const dialog = document.querySelector('.export-dialog')!;
      expect(dialog.textContent).toContain('Replace 2 existing files?');
      expect([...dialog.querySelectorAll('.export-replace-list li')].map((item) => item.textContent))
        .toEqual(['horn.csv', 'horn.json']);
      expect(dialog.textContent).toContain('Nothing has been written yet');
      await act(async () => { button('Replace').click(); });

      await expect(answer).resolves.toBe(true);
      expect(document.querySelector('.export-dialog')).toBeNull();
    });

    it('answers no when it is cancelled', async () => {
      const { answer } = await askReplace(['/exports/horn.csv']);

      expect(document.querySelector('.export-dialog')?.textContent)
        .toContain('Replace 1 existing file?');
      await act(async () => { button('Cancel').click(); });

      await expect(answer).resolves.toBe(false);
    });

    it('lists the first few and counts the rest', async () => {
      // A folder of forty is not a list anyone reads, and a modal that scrolls
      // past the buttons is worse than a count.
      await askReplace(Array.from({ length: 9 }, (_index, index) => `/d/file${index}.frd`));

      const items = [...document.querySelectorAll('.export-replace-list li')]
        .map((item) => item.textContent);
      expect(items).toEqual(['file0.frd', 'file1.frd', 'file2.frd', 'file3.frd', 'and 5 more']);
      expect(document.querySelector('.export-dialog')?.textContent)
        .toContain('Replace 9 existing files?');
    });

    it('is answered no when no dialog is mounted', async () => {
      // An unanswerable question is not consent to overwrite a folder WG does
      // not own.
      await expect(askToReplaceExports({ directory: '/d', paths: ['/d/a.csv'] }))
        .resolves.toBe(false);
    });
  });
});
