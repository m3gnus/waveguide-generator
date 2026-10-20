import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { Editor } from './SourceContourEditor';
import { sourceEditorApi } from '../api/sourceEditor';
import { hornAssemblyContext, standaloneContext, wooferAssemblyContext } from '../sketch/adapters/sourceContext';
import { INITIAL_ASSEMBLY } from './SourceAssemblyEditor';
import { expandedPreset } from '../sketch/adapters/presetTestSupport';
import { realSessionReady } from '../sketch/testSupport';
import { SketchSession } from '../sketch/session';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn() } }));
let host: HTMLDivElement, root: Root;
const documentSource = expandedPreset('dome', { radius_mm: 8, height_mm: 1, surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 });
const d = INITIAL_ASSEMBLY.dimensions;
const plug = { id: 'core', z0_mm: 2, z1_mm: 6, inner0_mm: 0, outer0_mm: 2, inner1_mm: 0, outer1_mm: 3 };
const context = (z = 2, throat = 8, present = true) => hornAssemblyContext(d, present ? [{ ...plug, z0_mm: z }] : [], undefined, throat);
beforeAll(realSessionReady);
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.mocked(sourceEditorApi.presets).mockResolvedValue([]);
  vi.mocked(sourceEditorApi.validate).mockImplementation(async document => ({ document, geometry_sha256: 'g', excitation_sha256: 'e', meridian: {} }));
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.restoreAllMocks(); });
const settle = async () => act(async () => { await vi.advanceTimersByTimeAsync(200); });
const button = (text: string) => [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') ?? b.textContent) === text)!;
const svg = () => host.querySelector('[aria-label="Sketch drawing"]')!;
const pointer = async (target: Element, type = 'pointerdown', x = 0, y = 0, shiftKey = false) => act(async () => target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, buttons: type === 'pointerup' ? 0 : 1, shiftKey })));
const point = (id: string) => host.querySelector(`.sketch-point[data-id="${id}"]`)!;
const changed = vi.fn();
const render = async (z = 2, radius = 8, present = true, stale = false) => { await act(async () => root.render(<Editor onClose={() => {}} initialDocument={documentSource} onDraftChange={changed} limit={{ kind: 'throat', radius, hard: true }} context={context(z, radius, present)} contextStale={stale}/>)); await settle(); };

it('renders exactly one context layer, finite horn datums and a locked throat marker', async () => {
  await render();
  expect(host.querySelectorAll('.sketch-context-layer')).toHaveLength(1);
  expect(host.querySelectorAll('.sketch-references')).toHaveLength(0);
  expect(host.querySelector('[data-context-id="rim-plane"] text')?.lastChild?.textContent).toBe('Throat plane (z = 0)');
  expect(host.querySelector('[data-context-id="rim-plane"] text')?.textContent).not.toMatch(/baffle/i);
  expect(host.querySelector('.sketch-limit text')?.textContent).toContain('◆ Throat r 8.00 🔒');
  expect(host.querySelector('.sketch-limit line')).toBeNull();
  expect(host.querySelector('[data-context-id="horn.wall"]')?.getAttribute('style')).toContain('pointer-events: none');
  expect(host.querySelector('[data-context-id="box.side.far"]')?.classList.contains('far')).toBe(true);
  await render(2, 8, true, true);
  expect(host.textContent).toContain('Context out of date');
  expect(host.querySelector('.sketch-context-layer')?.classList.contains('stale')).toBe(true);
});
it('uses host-specific and unhosted rim plane labels', async () => {
  for (const c of [wooferAssemblyContext(d), standaloneContext(undefined, 8)]) {
    await act(async () => root.render(<Editor onClose={() => {}} initialDocument={documentSource} context={c}/>)); await settle();
    expect(host.querySelector('[data-context-id="rim-plane"] text')?.lastChild?.textContent).toBe(c.rimPlaneLabel);
  }
});
it('dimensions the pole to the plug face, follows a host edit, reports deletion and supports undo', async () => {
  await render(); await act(async () => button('Dimension (D)').click());
  const pole = point('pole');
  await pointer(pole, 'pointerdown', Number(pole.getAttribute('cx')), Number(pole.getAttribute('cy')));
  await pointer(svg(), 'pointerup');
  await pointer(host.querySelector('[data-context-id="plug/core/inlet"] .sketch-context-hit')!);
  await settle();
  await pointer(svg(), 'pointermove', 300, 110);
  expect(host.querySelector('.sketch-dimension-preview')?.getAttribute('aria-label')).toBe('Preview pointLineDistance');
  await pointer(svg(), 'pointerdown', 300, 110); await pointer(svg(), 'pointerup', 300, 110); await settle();
  expect(host.querySelector('[aria-label="Dimension value"]')).not.toBeNull();
  await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  const solve = vi.spyOn(SketchSession.prototype, 'solveFull');
  await render(3);
  expect(solve).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('Reference context changed; contour updated');
  expect(changed.mock.calls.at(-1)![0].contour.points[0].z_mm).toBeCloseTo(2, 8);
  await render(3, 8, false);
  expect(host.textContent).toContain('Plug core inlet no longer exists. Re-attach or delete the constraint');
  await act(async () => button('Undo').click()); await settle();
  expect(host.querySelector('[aria-label^="Dimension pointLineDistance"]')).toBeNull();
});
it('lets the selection constraint menu use materialised context points', async () => {
  await render();
  const pole = point('pole'); await pointer(pole, 'pointerdown', Number(pole.getAttribute('cx')), Number(pole.getAttribute('cy'))); await pointer(svg(), 'pointerup');
  await pointer(host.querySelector('[data-context-id="plug/core/inlet.outer"] .sketch-context-hit')!, 'pointerdown', 0, 0, true);
  await settle();
  expect(button('Coincident')).toBeDefined();
});
it('shows the throat-update notice and holds the contour to the new hard radius', async () => {
  await render(2, 12.7); await render(2, 13);
  expect(host.textContent).toContain('Throat changed 12.70 → 13.00 mm; contour updated');
  expect(changed.mock.calls.at(-1)![0].contour.points.at(-1).r_mm).toBe(13);
});
it('fits nearby datums and plugs while excluding a distant box, and includes a nearby box', async () => {
  const show = async (large: boolean) => {
    const dimensions = { ...d, width_mm: large ? 4000 : 20, height_mm: large ? 4000 : 20,
      depth_mm: large ? 4000 : 20, horn_length_mm: large ? 2000 : 10, mouth_radius_mm: large ? 500 : 9,
      horn_xy_mm: [0, 0] as [number, number] };
    await act(async () => root.render(<Editor onClose={() => {}} initialDocument={documentSource} limit={{ kind: 'throat', radius: 8, hard: true }} context={hornAssemblyContext(dimensions, [plug], undefined, 8)}/>)); await settle();
    await act(async () => button('Fit (F)').click());
  };
  const pathCoordinates = (id: string) => host.querySelector(`[data-context-id="${id}"] > path:not(.sketch-context-hit)`)!.getAttribute('d')!.match(/-?[\d.]+/g)!.map(Number);
  await show(true);
  const inlet = pathCoordinates('plug/core/inlet');
  expect(inlet.every((n, i) => n >= 0 && n <= (i % 2 ? 360 : 640))).toBe(true);
  const far = pathCoordinates('box.side.near'); expect(far[0]).toBeGreaterThan(640);
  await show(false);
  const near = pathCoordinates('box.side.near');
  expect(near.every((n, i) => n >= 0 && n <= (i % 2 ? 360 : 640))).toBe(true);
});
