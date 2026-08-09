import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobsSnapshot } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { DesignFileMenu } from './DesignFileMenu';

/**
 * The export menu is the whole point of the CAD path: someone who wants a
 * waveguide in Fusion 360 finds it here or not at all. These render the real
 * menu and check that the default item asks the server for the manufacturable
 * solid, with the old inner-surface body still one click away.
 */

let container: HTMLDivElement;
let root: Root;
const requested: string[] = [];

beforeEach(() => {
  resetDesignStore();
  resetDocumentStore();
  preferencesStore.resetForTests();
  requested.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    requested.push(String(url));
    return new Response('ISO-10303-21;', {
      status: 200,
      headers: { 'Content-Type': 'model/step' },
    });
  }));
  vi.stubGlobal('URL', Object.assign(globalThis.URL, {
    createObjectURL: () => 'blob:step',
    revokeObjectURL: () => undefined,
  }));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

function open(): HTMLButtonElement[] {
  act(() => root.render(<DesignFileMenu/>));
  const chip = container.querySelector<HTMLButtonElement>('button.file-chip');
  act(() => chip?.click());
  const items = [...container.querySelectorAll<HTMLButtonElement>('.design-menu-item')];
  const exportItem = items.find((item) => item.textContent?.startsWith('Export'));
  act(() => exportItem?.click());
  return [...container.querySelectorAll<HTMLButtonElement>('.design-menu-item')];
}

function itemNamed(label: string): HTMLButtonElement {
  const found = open().find((item) => item.textContent?.startsWith(label));
  if (!found) throw new Error(`no export menu item named ${label}`);
  return found;
}

describe('design file export menu', () => {
  it('offers both STEP bodies', () => {
    const labels = open().map((item) => item.textContent ?? '');
    expect(labels.some((label) => label.startsWith('STEP solid'))).toBe(true);
    expect(labels.some((label) => label.startsWith('STEP inner surface'))).toBe(true);
  });

  it('requests the solid from the plain STEP item', async () => {
    const item = itemNamed('STEP solid');
    await act(async () => { item.click(); });
    expect(requested).toEqual(['/api/export/step?body=solid']);
  });

  it('requests the inner surface from the surface item', async () => {
    const item = itemNamed('STEP inner surface');
    await act(async () => { item.click(); });
    expect(requested).toEqual(['/api/export/step?body=surface']);
  });

  it('names the next run from a successfully opened standalone config', async () => {
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(JSON.stringify({
      dialect: 'ath', migrationsApplied: [],
      passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
      design: useDesignStore.getState().design,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.spyOn(jobsSocket, 'getSnapshot').mockReturnValue({
      connection: 'connected', epoch: 1, cursor: 1, error: null,
      jobs: [
        { label: '260808_horn_v14' },
        { label: 'horn_v09' },
      ],
    } as unknown as JobsSnapshot);

    act(() => root.render(<DesignFileMenu/>));
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [{ name: '260701_horn_v13.cfg', text: async () => 'Length = 120' }],
    });
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });

    expect(preferencesStore.getSnapshot()).toMatchObject({ outputName: 'horn', jobVersion: 15 });
    expect(useDocumentStore.getState().filename).toBe('260701_horn_v13.cfg');
  });

  it('does not change run naming when opening a config fails', async () => {
    preferencesStore.update({ outputName: 'keep-me', jobVersion: 7 });
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(
      JSON.stringify({ detail: 'invalid config' }),
      { status: 422, headers: { 'Content-Type': 'application/json' } },
    ));

    act(() => root.render(<DesignFileMenu/>));
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [{ name: 'should_not_replace_v99.cfg', text: async () => 'invalid' }],
    });
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });

    expect(preferencesStore.getSnapshot()).toMatchObject({ outputName: 'keep-me', jobVersion: 7 });
    expect(useDocumentStore.getState().filename).not.toBe('should_not_replace_v99.cfg');
  });
});
