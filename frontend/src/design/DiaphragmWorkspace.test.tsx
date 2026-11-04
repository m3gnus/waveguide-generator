import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { sourceEditorApi } from '../api/sourceEditor';
import { previewSocket } from '../api/previewSocket';
import { ProgramFault } from '../api/httpError';
import fixture from '../sketch/adapters/frontView.fixture.json';
import { expandedPreset } from '../sketch/adapters/presetTestSupport';
import { sourceContourAdapter, seed } from '../sketch/adapters/sourceContour';
import { realSessionReady } from '../sketch/testSupport';
import { commitAssembly, emptyAssembly, HF_RADIATOR_ID, setWooferShape, type DesignAssembly } from '../stores/assembly';
import { currentDocumentLoad, designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { FRONT_CONTEXT_DELAY_MS, FrontWorkspace } from './FrontWorkspace';

const solve = vi.fn().mockResolvedValue('submitted');
vi.mock('../shell/JobsCoordinator', () => ({ useOptionalSolveControl: () => ({ solve, disabled: false, title: 'Solve', actionError: null }) }));
let root: Root, host: HTMLDivElement;
let frames: Map<number, FrameRequestCallback>, frameId: number;
const w = (fixture.before.assembly as DesignAssembly).woofers![0];
const assembly = () => useDesignStore.getState().design.assembly!;
const steps = () => useDesignStore.temporal.getState().pastStates.length;
const tab = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(b => b.textContent === label);
const button = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === label)!;
const fetcher = vi.fn(async () => new Response(JSON.stringify({ ...fixture.before.context,
  baffle: { x0_mm: -400, x1_mm: 400, y0_mm: -600, y1_mm: 600, edge_mm: 18 } }))) as unknown as typeof fetch;
const document = () => expandedPreset('flat', { radius_mm: w.radius_mm });
function sketched(doc = document()) {
  return { kind: 'sketched' as const, diaphragm: doc,
    authoring: { ...(sourceContourAdapter.serializeAuthoring(seed(doc)) as object), document: doc } };
}
async function mount(strict = false) {
  await act(async () => { root.render(strict ? <StrictMode><FrontWorkspace onClose={vi.fn()} fetcher={fetcher}/></StrictMode> : <FrontWorkspace onClose={vi.fn()} fetcher={fetcher}/>); });
  await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 30)));
}
async function selectWoofer() { await act(async () => host.querySelector<HTMLButtonElement>('button.front-source')!.click()); }
async function change(id: string, value: string) {
  await act(async () => {
    const select = host.querySelector<HTMLSelectElement>(`#diaphragm-${id}`)!;
    select.value = value; select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
async function waitFor(assert: () => void) { await act(async () => { await vi.waitFor(assert); }); }
beforeAll(realSessionReady);
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  frames = new Map(); frameId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  resetDesignStore(); solve.mockClear();
  const design = designForFamily('R-OSSE'); design.enclosure.depth = 250; design.source.shape = 2;
  useDesignStore.getState().replaceDocument({ ...design, assembly: structuredClone(fixture.before.assembly) as DesignAssembly });
  const snapshot = previewSocket.getSnapshot();
  let key = '', current = snapshot;
  vi.spyOn(previewSocket, 'getSnapshot').mockImplementation(() => {
    const state = useDesignStore.getState(), next = `${currentDocumentLoad()}:${state.designRevision}:${state.design.source.shape}`;
    if (key !== next) {
      key = next; current = { ...snapshot, stale: false, displayedRevision: state.designRevision,
        frame: { documentLoad: currentDocumentLoad(), header: { previewMetadata: {
          source_cap_height_mm: state.design.source.shape === 2 ? 0 : undefined, dimensions_mm: { source_diameter: [25.4, 25.4] },
        } } } as unknown as NonNullable<typeof snapshot.frame> };
    }
    return current;
  });
  vi.spyOn(sourceEditorApi, 'expand').mockImplementation(async (shape, dimensions, identity) => {
    const doc = expandedPreset(shape, { surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0, ...dimensions });
    doc.contour.physical_source_id = identity.contour.physical_source_id; doc.contour.rim_id = identity.contour.rim_id;
    doc.drive.channel_id = identity.drive.channel_id; doc.drive.motion = identity.drive.motion;
    return { document: doc, geometry_sha256: '', excitation_sha256: '', meridian: {} };
  });
  host = window.document.createElement('div'); window.document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); resetDesignStore(); });

it('converts a woofer in one Undo, offers its tab and removes it when converted back', async () => {
  await mount(); expect(tab(w.label)).toBeUndefined(); await selectWoofer(); const before = steps();
  await change(w.id, 'sketched');
  expect(steps()).toBe(before + 1); expect(tab(w.label)).toBeTruthy(); expect(button('Edit profile')).toBeTruthy();
  expect(assembly().woofers![0].shape.diaphragm?.drive.motion).toBe('axial');
  expect(sourceEditorApi.expand).toHaveBeenCalledWith('flat', { radius_mm: w.radius_mm }, expect.anything());
  await change(w.id, 'ath_cone'); expect(tab(w.label)).toBeUndefined();
  await act(async () => useDesignStore.getState().undo()); expect(tab(w.label)).toBeTruthy();
  await act(async () => useDesignStore.getState().undo()); expect(assembly().woofers![0].shape).toEqual(w.shape);
});

it('leaves a recessed piston unchanged and hides a conversion that cannot preserve its collar', async () => {
  commitAssembly(setWooferShape(assembly(), w.id, { kind: 'piston', recess_mm: 7 }));
  await mount(); await selectWoofer();
  expect(host.querySelector(`#diaphragm-${w.id} option[value="sketched"]`)).toBeNull();
  expect(host.textContent).toContain('set Recess to 0 first');
  expect(assembly().woofers![0].shape).toEqual({ kind: 'piston', recess_mm: 7 });
  expect(sourceEditorApi.expand).not.toHaveBeenCalled();
});

it('seeds the ATH cone through the shared independent-dimension generator', async () => {
  commitAssembly(setWooferShape(assembly(), w.id, { kind: 'ath_cone' }));
  await mount(); await selectWoofer(); await change(w.id, 'sketched');
  expect(sourceEditorApi.expand).toHaveBeenCalledWith('cone', expect.objectContaining({ radius_mm: w.radius_mm, depth_mm: .4 * w.radius_mm, cap_radius_mm: .35 * w.radius_mm }), expect.anything());
  expect(assembly().woofers![0].shape.diaphragm?.contour.points.at(-1)?.r_mm).toBe(w.radius_mm);
});

it.each([1, -1])('uses the current mesher cap height without approximating classic HF (curvature %s)', async curvature => {
  useDesignStore.getState().updateValues({ 'source.shape': 1, 'source.curvature': curvature });
  const snapshot = previewSocket.getSnapshot();
  vi.spyOn(previewSocket, 'getSnapshot').mockReturnValue({ ...snapshot, stale: false,
    displayedRevision: useDesignStore.getState().designRevision,
    frame: { documentLoad: currentDocumentLoad(), header: { previewMetadata: { source_cap_height_mm: 2, dimensions_mm: { source_diameter: [25.4, 25.4] } } } } as unknown as NonNullable<typeof snapshot.frame> });
  await mount(); await change(HF_RADIATOR_ID, 'sketched');
  expect(sourceEditorApi.expand).toHaveBeenCalledWith('dome', { radius_mm: 12.7, height_mm: 2 }, expect.anything());
  expect(assembly().hf_source?.diaphragm?.contour.points[0].z_mm).toBe(2 * curvature);
});

it('keeps a classic spherical cap unchanged until its current preview is available', async () => {
  useDesignStore.getState().updateValue('source.shape', 1);
  await mount();
  expect(host.querySelector<HTMLOptionElement>(`#diaphragm-${HF_RADIATOR_ID} option[value="sketched"]`)!.disabled).toBe(true);
  expect(host.textContent).toContain('Enable Live update');
  expect(sourceEditorApi.expand).not.toHaveBeenCalled();
});

it('converts HF Classic ↔ Sketched and preserves the HF-only assembly', async () => {
  useDesignStore.getState().replaceDocument({ ...useDesignStore.getState().design, assembly: emptyAssembly() });
  await mount(); const before = steps(); await change(HF_RADIATOR_ID, 'sketched');
  expect(steps()).toBe(before + 1); expect(assembly().hf_source?.diaphragm?.contour.physical_source_id).toBe(HF_RADIATOR_ID);
  await act(async () => tab('HF diaphragm')!.click());
  await waitFor(() => expect(button('Add central plug')).toBeTruthy());
  await act(async () => button('Add central plug').click()); expect(assembly().hf_source?.phase_plugs).toHaveLength(1);
  await act(async () => tab('Front')!.click()); await change(HF_RADIATOR_ID, 'classic');
  expect(assembly().hf_source?.kind).toBe('classic'); expect(assembly().hf_source?.phase_plugs).toBeUndefined();
  await act(async () => useDesignStore.getState().undo()); expect(assembly().hf_source?.phase_plugs).toHaveLength(1);
});

it('drops a conversion response after Open instead of writing into the new design', async () => {
  let finish!: (value: Awaited<ReturnType<typeof sourceEditorApi.expand>>) => void;
  vi.mocked(sourceEditorApi.expand).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await mount(); await selectWoofer(); await change(w.id, 'sketched');
  const next = { ...useDesignStore.getState().design, assembly: emptyAssembly() };
  await act(async () => { useDesignStore.getState().replaceDocument(next); finish({ document: document(), geometry_sha256: '', excitation_sha256: '', meridian: {} }); });
  expect(assembly()).toEqual(next.assembly); expect(steps()).toBe(0);
});

it.each([new Error('Set Radius above 1 mm, then try again.'), new ProgramFault('internal contour invariant')])('keeps failed conversions atomic and uses the shared failure wording: %s', async reason => {
  vi.mocked(sourceEditorApi.expand).mockRejectedValueOnce(reason);
  await mount(); await selectWoofer(); const before = structuredClone(assembly()), history = steps();
  await change(w.id, 'sketched');
  expect(assembly()).toEqual(before); expect(steps()).toBe(history);
  expect(host.textContent).toContain(reason instanceof ProgramFault ? 'Keep the current diaphragm and try again.' : reason.message);
  expect(host.textContent).not.toContain('internal contour invariant');
});

it.each(['unfinished', 'valid'] as const)('refuses pending Solve with stored status %s, opens it, and reverts as one Undo step', async status => {
  const doc = document(), state = sourceContourAdapter.apply(seed(doc), { kind: 'deleteEntity', id: 'piston' });
  if ('error' in state) throw new Error(state.error);
  commitAssembly(setWooferShape(assembly(), w.id, { ...sketched(doc), status, authoring: { ...(sourceContourAdapter.serializeAuthoring(state) as object), document: doc } }));
  await mount();
  expect(button('Solve').disabled).toBe(true); expect(button('Solve').title).toBe(`Finish or revert the ${w.label} sketch before solving.`);
  await act(async () => button('Open sketch').click());
  expect(tab(w.label)?.getAttribute('aria-selected')).toBe('true');
  await waitFor(() => expect(host.textContent).toContain('unfinished'));
  const before = steps(); await act(async () => button('Revert').click());
  expect(steps()).toBe(before + 1); expect(assembly().woofers![0].shape.status).toBe('valid');
  expect(assembly().woofers![0].shape.diaphragm).toEqual(doc); expect(solve).not.toHaveBeenCalled();
  await act(async () => useDesignStore.getState().undo()); expect(assembly().woofers![0].shape.status).toBe(status);
});

it('makes one profile drag one project Undo and rehydrates after Undo/Redo', async () => {
  commitAssembly(setWooferShape(assembly(), w.id, sketched())); await mount(true);
  await act(async () => tab(w.label)!.click());
  await waitFor(() => expect(host.querySelector('.sketch-dof')?.textContent).not.toContain('…'));
  // Wait for context amendment before measuring the gesture.
  await act(() => new Promise(resolve => setTimeout(resolve, 2 * FRONT_CONTEXT_DELAY_MS + 60)));
  const svg = host.querySelector<SVGSVGElement>('svg[role="application"]')!;
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
  const point = host.querySelector<SVGCircleElement>('circle.sketch-point-hit[data-id="pole"]')!;
  const x = Number(point.getAttribute('cx')), y = Number(point.getAttribute('cy')), before = steps();
  const initial = structuredClone(assembly().woofers![0].shape);
  const pointer = (element: Element, type: string, py: number) => act(async () => { element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: py, buttons: type === 'pointerup' ? 0 : 1 })); });
  await pointer(point, 'pointerdown', y);
  for (const dy of [-10, -20, -30]) { await pointer(svg, 'pointermove', y + dy); await act(async () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(cb => cb(0)); }); }
  await pointer(svg, 'pointerup', y - 30);
  await waitFor(() => expect(useDesignStore.getState().dragSnapshot).toBeNull());
  expect(steps()).toBe(before + 1); const edited = structuredClone(assembly().woofers![0].shape);
  expect(edited).not.toEqual(initial);
  await act(async () => useDesignStore.getState().undo()); await waitFor(() => expect(assembly().woofers![0].shape).toEqual(initial));
  await act(async () => useDesignStore.getState().redo()); await waitFor(() => expect(assembly().woofers![0].shape).toEqual(edited));
});

it('closes a removed woofer tab on Open and never rebinds it to a different woofer', async () => {
  commitAssembly(setWooferShape(assembly(), w.id, sketched())); await mount(); await act(async () => tab(w.label)!.click());
  await act(async () => useDesignStore.getState().replaceDocument({ ...useDesignStore.getState().design, assembly: emptyAssembly() }));
  expect(tab(w.label)).toBeUndefined(); expect(tab('Front')?.getAttribute('aria-selected')).toBe('true');
});

it('does not rewrite saved geometry, authoring or history merely by opening its tab', async () => {
  const doc = expandedPreset('cone', { radius_mm: w.radius_mm, surround_width_mm: 0, land_width_mm: 0 });
  commitAssembly(setWooferShape(assembly(), w.id, sketched(doc))); await mount();
  const before = structuredClone(assembly()), history = steps();
  await act(async () => tab(w.label)!.click());
  await waitFor(() => expect(host.querySelector('.sketch-dof')?.textContent).toMatch(/\d+ DOF/));
  await act(() => new Promise(resolve => setTimeout(resolve, 2 * FRONT_CONTEXT_DELAY_MS)));
  expect(assembly()).toEqual(before); expect(steps()).toBe(history);
});

it('shows an unfinished sketch that has no committed geometry and can revert it', async () => {
  const doc = document(), state = sourceContourAdapter.apply(seed(doc), { kind: 'deleteEntity', id: 'piston' });
  if ('error' in state) throw new Error(state.error);
  commitAssembly(setWooferShape(assembly(), w.id, { kind: 'sketched', status: 'unfinished',
    authoring: { ...(sourceContourAdapter.serializeAuthoring(state) as object), document: null } }));
  await mount(); await act(async () => button('Open sketch').click());
  await waitFor(() => expect(host.textContent).toContain('unfinished'));
  await act(async () => button('Revert').click());
  expect(assembly().woofers![0].shape).toEqual({ kind: 'piston' }); expect(tab(w.label)).toBeUndefined();
});
