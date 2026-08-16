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
    createObjectURL: () => 'blob:step',
    revokeObjectURL: () => undefined,
  }));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  importedMeshStore.clear();
  workspaceModeStore.setMode('parametric');
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

  it('sends a saved design to CAD and reports its sequence and destination', async () => {
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

    expect(preferencesStore.getSnapshot()).toMatchObject({ outputName: '260701_horn_v13' });
    expect(preferencesStore.getSnapshot().nameSourceProjection).not.toBeNull();
    expect(useDocumentStore.getState().filename).toBe('260701_horn_v13.cfg');
    expect(useDocumentStore.getState()).toMatchObject({ identity: {
      designId: identity.designId,
      lineageId: identity.lineageId,
      baseEditVersion: 3,
    }, classification: 'stale_copy' });
    expect(container.querySelector('.cadlink-badge')?.textContent).toBe('stale copy');
    expect(container.querySelector('.cadlink-badge')?.getAttribute('title')).toContain('Saving will preserve both versions');
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

  it('adopts an auto-fork identity and reports it with the existing non-blocking toast', async () => {
    const original = {
      designId: 'wgd_01K00000000000000000000000',
      lineageId: 'wgl_01K00000000000000000000000',
      baseEditVersion: 3,
    };
    const fork = { ...original, designId: 'wgd_01K00000000000000000000001', baseEditVersion: 1 };
    useDocumentStore.getState().setFilename('copied-horn.cfg');
    useDocumentStore.getState().setCadLink(original, 'stale_copy');
    vi.mocked(fetch).mockImplementationOnce(async () => new Response(JSON.stringify({
      text: 'CadLink = {\n}\n',
      suggestedFilename: 'copied-horn.cfg',
      identity: fork,
      forked: true,
      from: { designId: original.designId, editVersion: 3, exportId: null },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    act(() => root.render(<DesignFileMenu/>));
    act(() => container.querySelector<HTMLButtonElement>('button.file-chip')!.click());
    const save = [...container.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent?.startsWith('Save'))!;
    await act(async () => { save.click(); });

    const request = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(request[1]?.body))).toMatchObject({ identity: original, filename: 'copied-horn.cfg' });
    expect(useDocumentStore.getState()).toMatchObject({ identity: fork, classification: 'current' });
    expect(container.querySelector('[role="status"]')?.textContent).toBe('Saved as a new fork of copied-horn');
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

  it('does not change run naming when opening a config fails', async () => {
    preferencesStore.update({ outputName: 'keep-me' });
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

    expect(preferencesStore.getSnapshot()).toMatchObject({ outputName: 'keep-me' });
    expect(useDocumentStore.getState().filename).not.toBe('should_not_replace_v99.cfg');
  });

  it('starts New without carrying the previous file identity', () => {
    useDocumentStore.getState().setFilename('old.cfg');
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
      filename: '', identity: null, classification: null,
    });
  });
});
