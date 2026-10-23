import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ParamPanel } from '../design/ParamPanel';
import { resetDesignStore } from '../stores/design';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';
import { useMeshImportStore } from '../stores/meshImport';
import { useImportedSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { cadWorkspace, emptyCadWorkspace } from '../stores/cadWorkspace';
import { preferencesStore } from '../prefs/preferences';
import { entryMatches } from './CommandPalette';
import { buildParameterPaletteEntries } from './TopBar';
import { resetWorkspaceNavigationForTests, workspaceNavigation } from './workspaceNavigation';
import { ImportedWorkspacePanel } from './ImportedWorkspacePanel';

let host: HTMLDivElement, root: Root, client: QueryClient;
const initialFile = useMeshImportStore.getState();
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetDesignStore(); resetCadReturnStore(); resetImportedModelStore();
  resetWorkspaceNavigationForTests();
  preferencesStore.resetForTests();
  useMeshImportStore.setState(initialFile, true);
  vi.stubGlobal('fetch', vi.fn(async input => new Response(JSON.stringify(String(input).includes('symmetry') ? { xz: true, yz: true, quadrants: 1, reasons: { xz: [], yz: [] } } : { items: [], engines: [] }))));
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  act(() => root.unmount()); client.clear(); host.remove();
  workspaceModeStore.setMode('parametric'); resetImportedModelStore(); resetCadReturnStore();
  useMeshImportStore.setState(initialFile, true); vi.restoreAllMocks(); vi.unstubAllGlobals();
});

it('shows one Fusion instance chooser in CAD Link', async () => {
  const selectFusionInstance = vi.fn();
  vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue({ ...emptyCadWorkspace, selectFusionInstance,
    fusionStatus: { cadFolderConfigured: true, running: true, state: 'instance_selection_required', link: null,
      matchingLinks: [{ instanceId: 'a' }, { instanceId: 'b' }] } as never,
  });
  workspaceModeStore.setMode('cad'); workspaceModeStore.setImportedInput('cad');
  await mount();
  expect(host.querySelectorAll('[aria-label="Linked Fusion instance"]')).toHaveLength(1);
  expect(host.querySelector('.linked-design-card')).toBeNull();
  const select = host.querySelector<HTMLSelectElement>('[aria-label="Linked Fusion instance"]')!;
  await act(async () => { select.value = 'b'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(selectFusionInstance).toHaveBeenCalledExactlyOnceWith('b');
});

it.each([
  ['not_linked', 'Open in Fusion 360'],
  ['stale', 'Send WG changes to Fusion'],
] as const)('shows one primary Fusion action for %s and uses the shared send', async (state, label) => {
  const sendWgToFusion = vi.fn().mockResolvedValue(null);
  vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue({ ...emptyCadWorkspace, sendWgToFusion,
    fusionStatus: { cadFolderConfigured: true, running: true, state, currentFormula: 'OSSE', wgChangesAvailable: state === 'stale',
      observationFreshness: 'current', link: state === 'stale' ? { instanceId: 'a' } : null } as never,
  });
  workspaceModeStore.setMode('cad'); workspaceModeStore.setImportedInput('cad');
  await mount();
  const actions = [...host.querySelectorAll('button')].filter(button => button.textContent === label);
  expect(actions).toHaveLength(1);
  expect(host.querySelector('.linked-design-card')).toBeNull();
  await act(async () => actions[0].click());
  expect(sendWgToFusion).toHaveBeenCalledExactlyOnceWith();
});

it('shows no Linked design card in parametric Geometry', async () => {
  workspaceModeStore.setMode('parametric');
  await mount();
  expect(host.querySelector('[data-param-tab="geometry"]')).not.toBeNull();
  expect(host.querySelector('[data-section="Linked design"]')).toBeNull();
  expect(host.querySelector('.linked-design-card')).toBeNull();
  expect(host.querySelector('.cadlink-panel')).toBeNull();
  expect(host.querySelector('[aria-label="Linked Fusion instance"]')).toBeNull();
});

it.each([false, true])('routes connection search to CAD Link with Geometry mounted=%s', async (mounted) => {
  vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue(emptyCadWorkspace);
  const entries = buildParameterPaletteEntries(undefined, { mode: 'cad' });
  const connection = entries.find(entry => entry.label === 'CAD Link')!;
  expect(entries.some(entry => entry.id === 'cad-control-cad.linked-design')).toBe(false);
  for (const query of ['Linked design', 'Fusion', 'Onshape', 'connection', 'sync', 'send', 'rebuild']) {
    expect(entryMatches(connection, query), query).toBe(true);
  }
  workspaceModeStore.setMode('parametric'); workspaceModeStore.setImportedInput('file');
  if (mounted) await mount();
  await act(async () => connection.run());
  if (!mounted) {
    expect(workspaceNavigation.getRevealTarget()).toBe('cad-link');
    await mount();
  }
  expect(workspaceModeStore.getSnapshot()).toMatchObject({ mode: 'cad', importedInput: 'cad' });
  const section = host.querySelector<HTMLElement>('[aria-label="CAD Link connection"]')!;
  expect(section).not.toBeNull();
  expect(section.querySelector('details')?.open).toBe(true);
  expect(document.activeElement).toBe(section);
  expect(workspaceNavigation.getRevealTarget()).toBeNull();
});
const mount = () => act(async () => root.render(<QueryClientProvider client={client}>
  <ImportedWorkspacePanel/><ParamPanel tab="simulation"/>
</QueryClientProvider>));

it('mounts file geometry and simulation controls once and preserves drafts across repeated switches', async () => {
  workspaceModeStore.setImportedInput('file'); workspaceModeStore.setMode('cad');
  useMeshImportStore.setState({ filename: '測定.msh', original: 'mesh', summary: { groups: [{ name: 'Source', tag: 17, triangle_count: 3 }] } as never });
  useImportedModelStore.setState({ input: 'file', fileDraft: { units: 'cm', forward: '+x', up: '+z', selected: [17] } });
  const draft = useImportedModelStore.getState().fileDraft;
  const sweep = useImportedSolveOptionsStore.getState().sweepCount;
  await mount();
  for (let index = 0; index < 3; index++) {
    expect(host.querySelectorAll('[aria-label="Mesh units"]')).toHaveLength(1);
    expect(host.querySelectorAll('[aria-label="Use Source (17) as a source"]')).toHaveLength(1);
    expect(host.querySelectorAll('[aria-label="Mesh frequency count"]')).toHaveLength(1);
    expect(host.querySelector('[aria-label="Imported mesh geometry"] [aria-label="Mesh frequency count"]')).toBeNull();
    expect(host.querySelector('[aria-label="Imported mesh simulation"] [aria-label="Mesh units"]')).toBeNull();
    expect(host.querySelector('.run-name-field')).toBeNull();
    await act(async () => workspaceModeStore.setImportedInput('cad'));
    expect(host.querySelectorAll('.cadlink-panel')).toHaveLength(1);
    expect(host.querySelectorAll('[aria-label="Imported mesh input"]')).toHaveLength(1);
    await act(async () => workspaceModeStore.setMode('parametric'));
    expect(host.querySelectorAll('[aria-label="Design name"]')).toHaveLength(1);
    expect(host.querySelector('.cadlink-panel')).toBeNull();
    await act(async () => { workspaceModeStore.setMode('cad'); workspaceModeStore.setImportedInput('file'); });
  }
  expect(useImportedModelStore.getState().fileDraft).toEqual(draft);
  expect(useImportedSolveOptionsStore.getState().sweepCount).toBe(sweep);
});
