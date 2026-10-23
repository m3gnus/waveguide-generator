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
import { ImportedWorkspacePanel } from './ImportedWorkspacePanel';

let host: HTMLDivElement, root: Root, client: QueryClient;
const initialFile = useMeshImportStore.getState();
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetDesignStore(); resetCadReturnStore(); resetImportedModelStore();
  useMeshImportStore.setState(initialFile, true);
  vi.stubGlobal('fetch', vi.fn(async input => new Response(JSON.stringify(String(input).includes('symmetry') ? { xz: true, yz: true, quadrants: 1, reasons: { xz: [], yz: [] } } : { items: [], engines: [] }))));
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(() => {
  act(() => root.unmount()); client.clear(); host.remove();
  workspaceModeStore.setMode('parametric'); resetImportedModelStore(); resetCadReturnStore();
  useMeshImportStore.setState(initialFile, true); vi.unstubAllGlobals();
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
