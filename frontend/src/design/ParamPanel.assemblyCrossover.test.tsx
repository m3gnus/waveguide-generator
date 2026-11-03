import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { solveModel } from '../jobs/actions';
import { designAssemblySnapshot } from '../jobs/assemblySolve';
import { compiledDesignAssembly } from '../jobs/assemblySolve.fixtures';
import { modelSolvePress } from '../jobs/importedSubmission';
import { latestCombine } from '../results/latestCombine';
import { buildParameterPaletteEntries } from '../shell/TopBar';
import { addWoofer, emptyAssembly } from '../stores/assembly';
import { assemblyContentKey, resetAssemblyBuild } from '../stores/assemblyBuild';
import { assemblyImportedSetup } from '../stores/assemblySimulation';
import { combineWire, resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { currentDocumentLoad, designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { adoptDesignBuild, resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { ParamPanel } from './ParamPanel';

let host: HTMLDivElement;
let root: Root;
let client: QueryClient;
let woofer: string;

function render() {
  act(() => root.render(<QueryClientProvider client={client}><ParamPanel tab="simulation" /></QueryClientProvider>));
}

/** Stand in for the geometry build; setup and request serialization remain real. */
function adoptBuild() {
  const result = compiledDesignAssembly();
  const { design, designRevision } = useDesignStore.getState();
  adoptDesignBuild({ model: result.model, record: { ...result.prepared,
    sources: [...result.prepared.sources, { id: `${woofer}/cap`, role: 'LF', radiator_id: woofer, segment_id: 'cap' }],
    excitations: [...result.prepared.excitations, { id: woofer, source_ids: [`${woofer}/cap`], motion: 'axial', patch_weights: {} }],
  } }, { designUid: useDocumentStore.getState().designUid ?? null, revision: designRevision,
    contentKey: assemblyContentKey(design), load: currentDocumentLoad() });
}

function reopen() {
  const saved = JSON.stringify(serializeDesign(useDesignStore.getState().design));
  act(() => {
    useDesignStore.getState().replaceDocument(hydrateDesignDocument(JSON.parse(saved)));
    adoptBuild();
  });
}

function toggle() {
  act(() => host.querySelector<HTMLInputElement>('#cad-combine')!.click());
}

function corner() {
  return host.querySelector<HTMLInputElement>('[data-control-reveal-id="cad.crossover.frequency"] input')!;
}

function editCorner(hz: string) {
  const input = corner();
  act(() => {
    input.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, hz);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  act(() => input.blur());
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  resetDocumentStore(); resetDesignStore(); resetCadReturnStore(); resetImportedModelStore();
  resetAssemblyBuild(); resetSolveOptionsStore(); latestCombine.reset();
  workspaceModeStore.setMode('parametric');
  const added = addWoofer(emptyAssembly(), { anchor: 'below' });
  woofer = added.id;
  useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: added.assembly });
  adoptBuild();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove(); client.clear(); latestCombine.reset(); vi.restoreAllMocks();
});

it('mounts the shared editor with the explicit new-assembly default without editing the document', () => {
  const before = structuredClone(useDesignStore.getState().design);
  const retained = useImportedModelStore.getState();
  const cad = useCadReturnStore.getState();
  render();
  expect(host.querySelectorAll('[data-section="Crossover"]')).toHaveLength(1);
  expect(buildParameterPaletteEntries().some(entry => entry.detail === 'Crossover')).toBe(true);
  expect(host.querySelector<HTMLInputElement>('#cad-combine')!.checked).toBe(true);
  expect(Number(corner().value)).toBe(combineWire(assemblyImportedSetup())!.channels[assemblyImportedSetup().driverGroups[1].id].lp!.fc_hz);
  expect(useDesignStore.getState().design).toEqual(before);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
  expect(useImportedModelStore.getState()).toBe(retained);
  expect(useCadReturnStore.getState()).toBe(cad);
});

it('shows an omitted saved crossover as off before and after save/reopen', () => {
  const design = structuredClone(useDesignStore.getState().design);
  delete design.assembly!.crossover;
  useDesignStore.getState().replaceDocument(design);
  adoptBuild();
  render();
  for (const check of [() => {}, reopen]) {
    check();
    expect(host.querySelector<HTMLInputElement>('#cad-combine')!.checked).toBe(false);
    expect(JSON.stringify(modelSolvePress(designAssemblySnapshot(), 'Assembly', 'omitted').setup)).not.toContain('"combine":');
    expect(useDesignStore.getState().design.assembly).not.toHaveProperty('crossover');
  }
});

it('turns the combination off and on with one undo step per edit, including after save/reopen', () => {
  render();
  toggle();
  expect(useDesignStore.getState().design.assembly!.crossover).toEqual({ enabled: false });
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  expect(combineWire(assemblyImportedSetup())).toBeUndefined();
  act(() => useDesignStore.getState().undo());
  expect(host.querySelector<HTMLInputElement>('#cad-combine')!.checked).toBe(true);
  expect(useDesignStore.getState().design.assembly!.crossover).toEqual({ enabled: true });
  act(() => useDesignStore.getState().redo());
  reopen();
  expect(host.querySelector<HTMLInputElement>('#cad-combine')!.checked).toBe(false);
  expect(JSON.stringify(modelSolvePress(designAssemblySnapshot(), 'Assembly', 'off').setup)).not.toContain('"combine":');
  toggle();
  expect(host.querySelector<HTMLInputElement>('#cad-combine')!.checked).toBe(true);
  expect(useDesignStore.getState().design.assembly!.crossover!.enabled).toBe(true);
});

it('persists corner and alignment edits through undo and save/reopen into the actual solve request', async () => {
  render();
  const initial = structuredClone(useDesignStore.getState().design.assembly);
  editCorner('1250');
  const edited = structuredClone(useDesignStore.getState().design.assembly);
  expect(edited!.crossover!.enabled).toBe(true);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  act(() => useDesignStore.getState().undo());
  expect(useDesignStore.getState().design.assembly).toEqual(initial);
  act(() => useDesignStore.getState().redo());
  const alignment = host.querySelector('[aria-label="Time-align members mode"]')!;
  act(() => [...alignment.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'Manual')!.click());
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(2);
  act(() => useDesignStore.getState().undo());
  expect(useDesignStore.getState().design.assembly).toEqual(edited);
  act(() => useDesignStore.getState().redo());
  const expected = combineWire(assemblyImportedSetup())!;
  const [low, high] = expected.members;
  expect(expected.channels[low].lp!.fc_hz).toBe(1250);
  expect(expected.channels[high].hp!.fc_hz).toBe(1250);
  expect(expected.channels[low].delay).toEqual({ mode: 'manual', ms: 0 });
  reopen();
  expect(Number(corner().value)).toBe(1250);
  expect(host.querySelector('[aria-label="Time-align members mode"] button[aria-pressed="true"]')!.textContent).toBe('Manual');
  const snapshot = designAssemblySnapshot();
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ job_id: 'assembly-edited' }));
  await solveModel(snapshot.modelId, modelSolvePress(snapshot, 'Assembly', 'edited'), fetcher);
  const submitted = JSON.parse(String(fetcher.mock.calls[0][1]!.body));
  expect(submitted.setup.geometry.combine).toEqual(expected);
  expect(submitted.design.assembly.crossover).toEqual(useDesignStore.getState().design.assembly!.crossover);
  expect(useCadReturnStore.getState().combineSpec).toBeNull();
  expect(useImportedModelStore.getState().fileSimulation).toBeNull();
});

it('finds the existing editor by its shared filter and alignment search terms', () => {
  render();
  for (const query of ['crossover', 'time align', 'filter']) {
    act(() => {
      const input = host.querySelector<HTMLInputElement>('#parameter-filter-simulation')!;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, query);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.querySelector('#cad-combine')).not.toBeNull();
    expect(host.textContent).not.toContain('No parameter labels or keys match');
  }
});

it('does not offer assembly crossover controls on a horn without an assembly', () => {
  useDesignStore.getState().replaceDocument(designForFamily('R-OSSE'));
  render();
  expect(host.querySelector('[data-section="Crossover"]')).toBeNull();
  expect(buildParameterPaletteEntries().some(entry => entry.detail === 'Crossover')).toBe(false);
});
