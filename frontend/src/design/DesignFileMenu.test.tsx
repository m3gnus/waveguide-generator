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
  const exportItem = items.find((item) => item.textContent?.startsWith('Export'));
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

describe('design file export menu', () => {
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
    // runs and the filename used by Download a copy.
    expect(useDocumentStore.getState()).toMatchObject({
      designName: '260701_horn_v13', filename: '260701_horn_v13.cfg',
    });
    expect(useDocumentStore.getState()).toMatchObject({ identity: {
      designId: identity.designId,
      lineageId: identity.lineageId,
      baseEditVersion: 3,
    }, classification: 'stale_copy' });
    expect(container.querySelector('.cadlink-badge')?.textContent).toBe('stale copy');
    expect(container.querySelector('.cadlink-badge')?.getAttribute('title')).toContain('Sending it to CAD will preserve both versions');
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
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    act(() => root.render(<DesignFileMenu/>));
    act(() => container.querySelector<HTMLButtonElement>('button.file-chip')!.click());
    const download = [...container.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent?.startsWith('Download a copy'))!;
    await act(async () => { download.click(); });

    const request = vi.mocked(fetch).mock.calls[0];
    expect(String(request[0])).toBe('/api/design/serialize');
    expect(JSON.parse(String(request[1]?.body))).not.toHaveProperty('identity');
    expect(useDocumentStore.getState()).toMatchObject(before);
    expect(container.querySelector('[aria-label="Unsaved changes"]')).not.toBeNull();
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
    expect(await (vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob).text()).toBe('serialized copy');
    expect(click).toHaveBeenCalledOnce();
    expect(container.querySelector('[role="status"]')?.textContent).toBe('Downloaded a copy as copied-horn.cfg');
  });

  it('labels browser serialization as Download a copy rather than Save', () => {
    const labels = open().map((item) => item.querySelector('span')?.textContent ?? '');

    expect(labels).toContain('Download a copy');
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
      .find((button) => button.textContent?.startsWith('Download a copy'))!;
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
