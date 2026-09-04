import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobsSnapshot } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import meshFixture from '../viewport/test-fixtures/tagged_sources-small.msh?raw';
import { CadLinkCoordinator } from '../shell/CadLinkCoordinator';
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
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetDesignStore();
  resetDocumentStore();
  resetSolveOptionsStore();
  preferencesStore.resetForTests();
  importedMeshStore.clear();
  workspaceModeStore.setMode('parametric');
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
    createObjectURL: vi.fn(() => 'blob:step'),
    revokeObjectURL: vi.fn(() => undefined),
  }));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  importedMeshStore.clear();
  workspaceModeStore.setMode('parametric');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function open(): HTMLButtonElement[] {
  // Send to CAD goes through the coordinator's unified path, so the real
  // coordinator is part of the unit under test. Its background status polls
  // are filtered out of request assertions with sendRequests().
  act(() => root.render(<><CadLinkCoordinator/><DesignFileMenu/></>));
  const chip = container.querySelector<HTMLButtonElement>('button.file-chip');
  act(() => chip?.click());
  const items = [...container.querySelectorAll<HTMLButtonElement>('.design-menu-item')];
  // Exactly 'Export', not a prefix: 'Export a copy' sits above the submenu
  // toggle and would otherwise be opened in its place.
  const exportItem = items.find((item) => item.querySelector('span')?.textContent === 'Export');
  act(() => exportItem?.click());
  return [...container.querySelectorAll<HTMLButtonElement>('.design-menu-item')];
}

function itemNamed(label: string): HTMLButtonElement {
  const found = open().find((item) => item.textContent?.startsWith(label));
  if (!found) throw new Error(`no export menu item named ${label}`);
  return found;
}

function sendRequests(): string[] {
  return requested.filter((path) => path === '/api/cad-workspace/path' || path === '/api/export/wglink');
}

function openedResponse(r: number) {
  return {
    dialect: 'ath', migrationsApplied: [],
    passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
    design: { ...useDesignStore.getState().design, R: r },
    cadlink: { identity: null, classification: 'missing', adoptionCandidate: null },
  };
}

async function chooseLocalDesign(file: { name: string; text: () => Promise<string> }) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, 'files', { configurable: true, value: [file] });
  await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });
}

describe('design file export menu', () => {
  it('confirms only after parsing before replacing an unsaved local design', async () => {
    useDesignStore.getState().updateField('R', 321);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/design/open') return new Response(JSON.stringify(openedResponse(999)), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('not found', { status: 404 });
    });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    act(() => root.render(<DesignFileMenu/>));

    await chooseLocalDesign({ name: 'replacement.cfg', text: async () => 'R = 999' });
    expect(confirm).toHaveBeenLastCalledWith('Discard unsaved changes and open replacement.cfg?');
    expect(useDesignStore.getState().design.R).toBe(321);
    expect(useDesignStore.getState().designRevision).toBe(2);

    await chooseLocalDesign({ name: 'replacement.cfg', text: async () => 'R = 999' });
    expect(useDesignStore.getState().design.R).toBe(999);
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'replacement', savedRevision: 3 });
  });

  it('does not apply a delayed open response over an edit made while it was pending', async () => {
    let resolveOpen!: (response: Response) => void;
    const pendingOpen = new Promise<Response>((resolve) => { resolveOpen = resolve; });
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => (
      String(input) === '/api/design/open'
        ? pendingOpen
        : Promise.resolve(new Response('not found', { status: 404 }))
    ));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    act(() => root.render(<DesignFileMenu/>));
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [{ name: 'delayed.cfg', text: async () => 'R = 999' }],
    });
    act(() => input.dispatchEvent(new Event('change', { bubbles: true })));
    await Promise.resolve();

    act(() => useDesignStore.getState().updateField('R', 321));
    await act(async () => {
      resolveOpen(new Response(JSON.stringify(openedResponse(999)), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(confirm).toHaveBeenCalledWith('Discard unsaved changes and open delayed.cfg?');
    expect(useDesignStore.getState().design.R).toBe(321);
    expect(useDocumentStore.getState().designName).toBe('');
  });

  it('imports standalone meshes from the file menu instead of the viewport toolbar', async () => {
    act(() => root.render(<DesignFileMenu/>));
    const input = container.querySelector<HTMLInputElement>('[aria-label="Import Gmsh mesh file"]');
    const file = new File([meshFixture], 'reference.msh', { type: 'text/plain' });
    Object.defineProperty(input, 'files', { configurable: true, value: [file] });

    await act(async () => { input?.dispatchEvent(new Event('change', { bubbles: true })); });

    expect(importedMeshStore.getSnapshot().file?.name).toBe('reference.msh');
    expect(importedMeshStore.getSnapshot().showing).toBe('file');
    expect(container.querySelector('[role="status"]')?.textContent).toContain('2 triangles');
  });

  it('offers both STEP bodies', () => {
    const labels = open().map((item) => item.textContent ?? '');
    expect(labels.some((label) => label.startsWith('STEP solid'))).toBe(true);
    expect(labels.some((label) => label.startsWith('STEP inner surface'))).toBe(true);
  });

  it('hides the Fusion-transport Send to CAD while Onshape is the CAD application', () => {
    act(() => preferencesStore.update({ cadApplication: 'onshape' }));
    const labels = open().map((item) => item.textContent ?? '');
    expect(labels.some((label) => label.startsWith('Send to CAD'))).toBe(false);
    expect(labels.some((label) => label.startsWith('STEP solid'))).toBe(true);
  });

  it('requests the solid from the plain STEP item', async () => {
    const item = itemNamed('STEP solid');
    await act(async () => { item.click(); });
    expect(requested.filter((path) => path.startsWith('/api/export/'))).toEqual(['/api/export/step?body=solid']);
  });

  it('requests the inner surface from the surface item', async () => {
    const item = itemNamed('STEP inner surface');
    await act(async () => { item.click(); });
    expect(requested.filter((path) => path.startsWith('/api/export/'))).toEqual(['/api/export/step?body=surface']);
  });

  it('surfaces an STL fidelity warning after reporting the successful write', async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      requested.push(path);
      if (path === '/api/export/stl') {
        const response = new Response('stl', {
          status: 200,
          headers: { 'X-Export-Warning': 'fine detail was coarsened' },
        });
        return Object.assign(response, {
          blob: async () => new Blob(['stl'], { type: 'application/sla' }),
        });
      }
      if (path === '/api/workspace/write-export') {
        return new Response(JSON.stringify({
          directory: 'C:/Output/horn', files: ['horn.stl'],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('not found', { status: 404 });
    });

    const item = itemNamed('STL');
    await act(async () => { item.click(); });

    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      'Exported STL from revision 1 to C:/Output/horn. Warning: fine detail was coarsened',
    );
  });

  it('sends a linked design to CAD and reports its sequence and destination', async () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 3,
    }, 'current');
    vi.mocked(fetch).mockImplementation(async (url: string | URL | Request) => {
      const path = String(url);
      requested.push(path);
      if (path === '/api/cad-workspace/path') {
        return new Response(JSON.stringify({ selected: true, path: '/cad-library' }));
      }
      return new Response(JSON.stringify({
        bundlePath: '/cad-library/wglink/tritonia_mk2.wglink', bundleId: 'wgb_1',
        exportId: 'wge_1', sequence: 7, designHash: 'sha256:d',
        geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    const item = itemNamed('Send to CAD');
    await act(async () => { item.click(); });

    expect(sendRequests()).toEqual(['/api/cad-workspace/path', '/api/export/wglink']);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      'Sent to CAD · sequence 7 · /cad-library/wglink/tritonia_mk2.wglink',
    );
  });

  it('does not start a second CAD export on a double click', async () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 3,
    }, 'current');
    vi.mocked(fetch).mockImplementation(async (url: string | URL | Request) => {
      const path = String(url);
      requested.push(path);
      if (path === '/api/cad-workspace/path') {
        return new Response(JSON.stringify({ selected: true, path: '/cad-library' }));
      }
      return new Response(JSON.stringify({
        bundlePath: '/cad-library/wglink/tritonia_mk2.wglink', bundleId: 'wgb_1',
        exportId: 'wge_1', sequence: 7, designHash: 'sha256:d',
        geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    const item = itemNamed('Send to CAD');
    await act(async () => {
      item.click();
      item.click();
    });

    expect(sendRequests()).toEqual(['/api/cad-workspace/path', '/api/export/wglink']);
  });

  it('sends an unsaved design and adopts the identity the server committed', async () => {
    vi.mocked(fetch).mockImplementation(async (url: string | URL | Request) => {
      const path = String(url);
      requested.push(path);
      if (path === '/api/cad-workspace/path') {
        return new Response(JSON.stringify({ selected: true, path: '/cad-library' }));
      }
      return new Response(JSON.stringify({
        bundlePath: '/cad-library/wglink/tritonia_mk2.wglink', bundleId: 'wgb_1',
        exportId: 'wge_1', sequence: 1, designHash: 'sha256:d',
        geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
        identity: {
          designId: 'wgd_01K00000000000000000000000',
          lineageId: 'wgl_01K00000000000000000000000',
          baseEditVersion: 1,
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    const item = itemNamed('Send to CAD');
    await act(async () => { item.click(); });

    expect(sendRequests()).toEqual(['/api/cad-workspace/path', '/api/export/wglink']);
    expect(useDocumentStore.getState().identity?.baseEditVersion).toBe(1);
    expect(useDocumentStore.getState().classification).toBe('current');
  });

  it('names the next run from a successfully opened standalone config', async () => {
    const identity = {
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 3,
      editVersion: 3,
      savedAt: '2026-08-10T10:00:00Z',
      savedDesignHash: 'sha256:0123456789abcdef',
      schema: 1,
    };
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(JSON.stringify({
      dialect: 'ath', migrationsApplied: [],
      passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
      design: useDesignStore.getState().design,
      cadlink: { identity, classification: 'stale_copy', adoptionCandidate: null },
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

    // One name: the opened file names the design, and the design names the
    // runs and the filename used by Export a copy.
    expect(useDocumentStore.getState()).toMatchObject({
      designName: '260701_horn_v13', filename: '260701_horn_v13.cfg',
    });
    expect(useDocumentStore.getState()).toMatchObject({ identity: {
      designId: identity.designId,
      lineageId: identity.lineageId,
      baseEditVersion: 3,
    }, classification: 'stale_copy' });
    expect(container.querySelector('.cadlink-badge')?.textContent).toBe('stale copy');
    expect(container.querySelector('.cadlink-badge')?.getAttribute('title')).toContain('Open the current head under CAD-linked designs');
  });

  it('restores directivity controls from old ATH polar blocks on open', async () => {
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(JSON.stringify({
      dialect: 'ath', migrationsApplied: [],
      passthrough: { keysPreserved: [], blocksPreserved: ['ABEC.Polars:SPL_H', 'ABEC.Polars:SPL_V'], keyCount: 0, blockCount: 2 },
      design: {
        ...useDesignStore.getState().design,
        extra_blocks: {
          'ABEC.Polars:SPL_H': { items: { MapAngleRange: '-20,100,25', Distance: '3', NormAngle: '8' }, lines: [] },
          'ABEC.Polars:SPL_V': { items: { MapAngleRange: '-20,100,25', Distance: '3', NormAngle: '8', Inclination: '270' }, lines: [] },
        },
      },
      cadlink: { identity: null, classification: 'missing', adoptionCandidate: null },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    act(() => root.render(<DesignFileMenu/>));
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [{ name: 'old-ath.cfg', text: async () => 'old ATH text' }],
    });
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });

    expect(useSolveOptionsStore.getState().polar).toMatchObject({
      angleStart: -20, angleEnd: 100, angleStep: 5,
      distance: 3, normAngle: 8, enabledAxes: ['horizontal', 'vertical'],
    });
  });

  it('downloads a serialized copy without changing identity or the saved baseline', async () => {
    const original = {
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 3,
    };
    useDocumentStore.getState().setDesignName('copied-horn');
    useDocumentStore.getState().setCadLink(original, 'stale_copy');
    useDocumentStore.getState().markSaved(2, 'saved-settings-baseline');
    useDesignStore.setState({ designRevision: 3 });
    const before = {
      identity: useDocumentStore.getState().identity,
      classification: useDocumentStore.getState().classification,
      savedRevision: useDocumentStore.getState().savedRevision,
      savedSettings: useDocumentStore.getState().savedSettings,
      savedDesignName: useDocumentStore.getState().savedDesignName,
    };
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(JSON.stringify({
      text: 'serialized copy',
      suggestedFilename: 'copied-horn.cfg',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(JSON.stringify({
      directory: 'C:/Output/copied-horn',
      files: ['copied-horn.cfg'],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    act(() => root.render(<DesignFileMenu/>));
    act(() => container.querySelector<HTMLButtonElement>('button.file-chip')!.click());
    const download = [...container.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent?.startsWith('Export a copy'))!;
    await act(async () => { download.click(); });

    const request = vi.mocked(fetch).mock.calls[0];
    expect(String(request[0])).toBe('/api/design/serialize');
    expect(JSON.parse(String(request[1]?.body))).not.toHaveProperty('identity');
    expect(useDocumentStore.getState()).toMatchObject(before);
    expect(container.querySelector('[aria-label="Unsaved changes"]')).not.toBeNull();
    // The copy is written into the output folder, never handed to the browser:
    // an `<a download>` reaches nobody in the desktop WebView2 window, which is
    // how a successful export could look like one that never happened.
    const write = vi.mocked(fetch).mock.calls[1];
    expect(String(write[0])).toBe('/api/workspace/write-export');
    const form = write[1]?.body as FormData;
    expect(form.get('subdirectory')).toBe('copied-horn');
    expect(form.get('relative_path')).toBe('copied-horn.cfg');
    expect(await (form.get('file') as File).text()).toBe('serialized copy');
    expect(click).not.toHaveBeenCalled();
    expect(container.querySelector('[role="status"]')?.textContent)
      .toBe('Exported a copy as copied-horn.cfg to C:/Output/copied-horn');
  });

  // Not "Save": the copy deliberately leaves the design unsaved (see above),
  // and not "Download" either -- it is written into the output folder, and
  // the desktop window has no download for it to mean.
  it('labels serialization as Export a copy, never Save', () => {
    const labels = open().map((item) => item.querySelector('span')?.textContent ?? '');

    expect(labels).toContain('Export a copy');
    expect(labels).not.toContain('Save');
  });

  it('opens the current registry head from the CAD-linked design picker', async () => {
    const identity = {
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 7,
      editVersion: 7,
      savedAt: '2026-08-20T12:00:00Z',
      savedDesignHash: 'sha256:0123456789abcdef',
      schema: 1,
    };
    vi.mocked(fetch).mockImplementation(async (url: string | URL | Request) => {
      const path = String(url);
      if (path === '/api/cadlink/designs') return new Response(JSON.stringify({ items: [{
        designId: identity.designId, lineageId: identity.lineageId, editVersion: 7,
        designHash: 'sha256:full', filename: 'registry-head.cfg',
        branchedFromDesignId: null, branchedFromEditVersion: null,
        exportCount: 3, lastExportedAt: '2026-08-20T11:00:00Z',
        createdAt: '2026-08-19T10:00:00Z', updatedAt: '2026-08-20T12:00:00Z',
      }, {
        designId: 'wgd_01K000000000000000ABCDEF', lineageId: 'wgl_other', editVersion: 2,
        designHash: 'sha256:other', filename: 'registry-head.cfg',
        branchedFromDesignId: null, branchedFromEditVersion: null,
        exportCount: 1, lastExportedAt: '2026-08-18T11:00:00Z',
        createdAt: '2026-08-18T10:00:00Z', updatedAt: '2026-08-18T12:00:00Z',
      }] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (path === `/api/cadlink/designs/${identity.designId}`) return new Response(JSON.stringify({
        designId: identity.designId, lineageId: identity.lineageId, editVersion: 7,
        filename: 'registry-head.cfg', updatedAt: '2026-08-20T12:00:00Z', text: 'registry snapshot',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (path === '/api/design/open') return new Response(JSON.stringify({
        dialect: 'ath', migrationsApplied: [],
        passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
        design: useDesignStore.getState().design,
        cadlink: { identity, classification: 'current', adoptionCandidate: null },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      throw new Error(`Unexpected request ${path}`);
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    act(() => root.render(<DesignFileMenu/>));
    act(() => container.querySelector<HTMLButtonElement>('button.file-chip')!.click());
    const picker = [...container.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent?.startsWith('CAD-linked designs'))!;
    await act(async () => { picker.click(); await Promise.resolve(); });
    const project = [...container.querySelectorAll<HTMLButtonElement>('[aria-label="CAD-linked designs"] button')][0];
    expect(project.textContent).toContain('registry-head.cfg');
    expect(project.textContent).toContain('v7 · 3 exports');
    expect(project.textContent).toContain('ID …000000');
    expect([...container.querySelectorAll<HTMLButtonElement>('[aria-label="CAD-linked designs"] button')][1].textContent)
      .toContain('ID …ABCDEF');

    await act(async () => { project.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(useDocumentStore.getState()).toMatchObject({
      designName: 'registry-head',
      identity: {
        designId: identity.designId,
        lineageId: identity.lineageId,
        baseEditVersion: 7,
      },
      classification: 'current',
    });
    expect(container.querySelector('[role="status"]')?.textContent)
      .toContain('Opened CAD-linked design registry-head.cfg');
  });

  it('reports serialization failure without downloading or changing saved state', async () => {
    useDocumentStore.getState().setDesignName('keep-unsaved');
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 4,
    }, 'current');
    useDocumentStore.getState().markSaved(4, 'saved-settings-baseline');
    useDesignStore.setState({ designRevision: 5 });
    const before = useDocumentStore.getState();
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(
      JSON.stringify({ detail: 'Could not serialize this design.' }),
      { status: 422, headers: { 'Content-Type': 'application/json' } },
    ));
    act(() => root.render(<DesignFileMenu/>));
    act(() => container.querySelector<HTMLButtonElement>('button.file-chip')!.click());
    const download = [...container.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent?.startsWith('Export a copy'))!;
    await act(async () => { download.click(); });

    expect(String(vi.mocked(fetch).mock.calls[0][0])).toBe('/api/design/serialize');
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(useDocumentStore.getState()).toMatchObject({
      identity: before.identity,
      classification: before.classification,
      savedRevision: before.savedRevision,
      savedSettings: before.savedSettings,
      savedDesignName: before.savedDesignName,
    });
    expect(container.querySelector('[aria-label="Unsaved changes"]')).not.toBeNull();
    expect(container.querySelector('[role="status"]')?.textContent).toBe('Could not serialize this design.');
  });

  it('offers hash-based identity re-adoption without blocking open', async () => {
    const candidate = {
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 6,
      filename: 'registered.cfg',
    };
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(JSON.stringify({
      dialect: 'mwg', migrationsApplied: [],
      passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
      design: useDesignStore.getState().design,
      cadlink: { identity: null, classification: 'missing', adoptionCandidate: candidate },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    act(() => root.render(<DesignFileMenu/>));
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [{ name: 'untagged.cfg', text: async () => 'Length = 120' }],
    });
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });

    expect(useDocumentStore.getState()).toMatchObject({ identity: null, classification: 'missing' });
    const adopt = container.querySelector<HTMLButtonElement>('[role="status"] button')!;
    expect(adopt.textContent).toBe('Re-adopt CAD link');
    act(() => adopt.click());
    expect(useDocumentStore.getState()).toMatchObject({ identity: {
      designId: candidate.designId,
      lineageId: candidate.lineageId,
      baseEditVersion: candidate.baseEditVersion,
    }, classification: 'current' });
  });

  it('does not rename the design when opening a config fails', async () => {
    useDocumentStore.getState().setDesignName('keep-me');
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

    expect(useDocumentStore.getState()).toMatchObject({
      designName: 'keep-me', filename: 'keep-me.cfg',
    });
  });

  it('starts New without carrying the previous file identity', () => {
    useDocumentStore.getState().setDesignName('old');
    // Renaming is unsaved work now, so New asks before discarding it.
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 5,
    }, 'current');
    act(() => root.render(<DesignFileMenu/>));
    act(() => container.querySelector<HTMLButtonElement>('button.file-chip')!.click());
    const create = [...container.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent?.startsWith('New'))!;
    act(() => create.click());
    // A new design is untitled. This used to assert the filename came back as
    // a specific .cfg, which is the bug written down: New produced a document
    // named after someone's test fixture, and the tab, the file chip and the
    // viewport title all repeated it.
    expect(useDocumentStore.getState()).toMatchObject({
      designName: '', filename: '', identity: null, classification: null,
    });
  });
});
