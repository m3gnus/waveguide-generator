import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { Editor } from './SourceContourEditor';
import { sourceEditorApi } from '../api/sourceEditor';
import { hornAssemblyContext, standaloneContext, wooferAssemblyContext } from '../sketch/adapters/sourceContext';
import { INITIAL_ASSEMBLY } from './SourceAssemblyEditor';
import { expandedPreset } from '../sketch/adapters/presetTestSupport';
import { realSessionReady } from '../sketch/testSupport';
import { seedPreset } from '../sketch/adapters/sourcePreset';
import { SketchCanvas } from '../sketch/SketchCanvas';
import { SketchSession } from '../sketch/session';
import { emptySketch } from '../sketch/model';
import type { ContextItem } from '../sketch/context';
import { namespaceStorage } from '../stores/durableSettings';

vi.mock('../api/sourceEditor', () => ({ sourceEditorApi: { validate: vi.fn(), presets: vi.fn() } }));
let host: HTMLDivElement, root: Root;
const documentSource = expandedPreset('dome', { radius_mm: 8, height_mm: 1, surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 });
const d = INITIAL_ASSEMBLY.dimensions;
const plug = { id: 'core', z0_mm: 2, z1_mm: 6, inner0_mm: 0, outer0_mm: 2, inner1_mm: 0, outer1_mm: 3 };
const context = (z = 2, throat = 8, present = true) => hornAssemblyContext(d, present ? [{ ...plug, z0_mm: z }] : [], undefined, throat);
beforeAll(realSessionReady);
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); namespaceStorage('sketchView').removeItem('');
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
  expect(host.querySelector('[data-context-id="rim-plane"] > title')?.textContent).toBe('Throat plane (z = 0.00)');
  expect([...host.querySelectorAll('.sketch-context-item text')].map(t => t.lastChild?.textContent)).toEqual(['Throat r 8.00 🔒']);
  expect(host.querySelector('[data-context-id="rim-plane"] > title')?.textContent).not.toMatch(/baffle/i);
  expect(host.querySelector('.sketch-limit text')?.textContent).toContain('Throat r 8.00 🔒');
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
    expect(host.querySelector('[data-context-id="rim-plane"] > title')?.textContent).toBe(c.rimPlaneLabel);
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
  expect(host.textContent).toContain('Throat changed 12.70 → 13.00 mm; dimensions kept');
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

it('keeps Names labels within 8 px of their visible geometry, without overlapping dimensions or each other', async () => {
  const dimensions = { ...d, width_mm: 500, height_mm: 600, depth_mm: 240, horn_length_mm: 140, mouth_radius_mm: 90 };
  const state = seedPreset(documentSource, 'dome', { radius_mm: 8, height_mm: 1, surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 });
  await act(async () => root.render(<SketchCanvas sketch={state.sketch} references={state.meta.references} problems={[]} context={hornAssemblyContext(dimensions, [], undefined, 12.7)}/>)); await settle();
  await act(async () => button('Fit all (Shift+F)').click());
  await act(async () => button('Names').click());
  const labels = [...host.querySelectorAll<SVGTextElement>('.sketch-context-item text')];
  expect(labels.length).toBeGreaterThan(1);
  const boxes = labels.map(text => {
    const x = Number(text.getAttribute('x')), y = Number(text.getAttribute('y'));
    const ax = Number(text.getAttribute('data-anchor-x')), ay = Number(text.getAttribute('data-anchor-y'));
    expect(ax).toBeGreaterThanOrEqual(0); expect(ax).toBeLessThanOrEqual(640);
    expect(ay).toBeGreaterThanOrEqual(0); expect(ay).toBeLessThanOrEqual(360);
    expect(Math.hypot(x - ax, y - ay)).toBeLessThanOrEqual(8);
    const path = text.parentElement!.querySelector('path:not(.sketch-context-hit)')!.getAttribute('d')!;
    const geometry = [...path.matchAll(/[ML](-?[\d.e+]+),(-?[\d.e+]+)/g)].map(m => ({ x: Number(m[1]), y: Number(m[2]) }));
    if (geometry.length === 1) geometry[0].y += 4; // Diamond starts above its centre.
    const distance = geometry.length === 1 ? Math.hypot(x - geometry[0].x, y - geometry[0].y) : Math.min(...geometry.slice(1).map((b, i) => {
      const a = geometry[i], dx = b.x - a.x, dy = b.y - a.y;
      const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / (dx * dx + dy * dy || 1)));
      return Math.hypot(x - a.x - t * dx, y - a.y - t * dy);
    }));
    expect(distance).toBeLessThanOrEqual(8);

    const label = text.lastChild!.textContent!, width = label.length * 6.4 + 4, height = 14;
    const anchor = text.getAttribute('text-anchor'), baseline = text.getAttribute('dominant-baseline');
    return { x: x - (anchor === 'middle' ? width / 2 : anchor === 'end' ? width : 0), y: y - (baseline === 'central' ? height / 2 : baseline === 'text-after-edge' ? height : 0), width, height };
  });
  for (const text of host.querySelectorAll<SVGTextElement>('.sketch-dimension text')) {
    const rect = text.previousElementSibling!, x = Number(text.getAttribute('x')), y = Number(text.getAttribute('y'));
    const rx = Number(rect.getAttribute('x')), ry = Number(rect.getAttribute('y')), width = Number(rect.getAttribute('width')), height = Number(rect.getAttribute('height'));
    boxes.push(text.parentElement!.getAttribute('transform')?.startsWith('rotate(-90')
      ? { x: x + ry - y, y: y - (rx - x + width), width: height, height: width } : { x: rx, y: ry, width, height });
  }
  expect(host.querySelectorAll('.sketch-dimension text').length).toBeGreaterThan(0);
  for (const [i, a] of boxes.entries()) for (const b of boxes.slice(i + 1)) expect(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y).toBe(true);
  expect(host.querySelector('[data-context-id="horn.mouth"] text')?.textContent).toContain('Mouth r 90.00');
  expect(host.querySelector('[data-context-id="horn.mouth"] > title')?.textContent).toContain('Mouth');
  expect(host.querySelector('[data-context-id="baffle.front"] > title')?.textContent).toBe('Front baffle (z = 140.00)');
});
it('fits the mouth and enclosure back with Shift+F and the Fit all button', async () => {
  const dimensions = { ...d, width_mm: 800, height_mm: 1000, depth_mm: 600, horn_length_mm: 140, mouth_radius_mm: 90 };
  await act(async () => root.render(<Editor onClose={() => {}} initialDocument={documentSource} limit={{ kind: 'throat', radius: 12.7, hard: true }} context={hornAssemblyContext(dimensions, [], undefined, 12.7)}/>)); await settle();
  for (const action of [() => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'F', shiftKey: true, bubbles: true })), () => button('Fit all (Shift+F)').click()]) {
    await act(async () => action());
    for (const id of ['horn.mouth', 'box.back']) {
      const path = host.querySelector(`[data-context-id="${id}"] > path:not(.sketch-context-hit)`)!.getAttribute('d')!;
      const coordinates = [...path.matchAll(/[ML](-?[\d.e+]+),(-?[\d.e+]+)/g)];
      expect(coordinates.length).toBeGreaterThan(0);
      for (const [, x, y] of coordinates) { expect(Number(x)).toBeGreaterThanOrEqual(0); expect(Number(x)).toBeLessThanOrEqual(640); expect(Number(y)).toBeGreaterThanOrEqual(0); expect(Number(y)).toBeLessThanOrEqual(360); }
    }
  }
});
it('lets a Line pointerdown on a context hit path start a line there', async () => {
  await render(); await act(async () => button('Line (L)').click());
  const hit = host.querySelector('[data-context-id="rim-plane"] .sketch-context-hit')!;
  expect((hit as SVGElement).style.pointerEvents).toBe('none');
  await act(async () => hit.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 250, clientY: 200, buttons: 1, ctrlKey: true }))); await pointer(svg(), 'pointerup', 250, 200);
  await pointer(svg(), 'pointermove', 310, 220);
  expect(host.querySelector('.sketch-preview')).not.toBeNull();
  expect(Number(host.querySelector('.sketch-preview')?.getAttribute('x1'))).toBeCloseTo(250);
  expect(Number(host.querySelector('.sketch-preview')?.getAttribute('y1'))).toBeCloseTo(200);
});
it('highlights selected materialised segments and semantic endpoints', async () => {
  await render();
  await pointer(host.querySelector('[data-context-id="plug/core/inlet"] .sketch-context-hit')!); await settle();
  expect(host.querySelector('[data-context-id="plug/core/inlet"]')?.classList.contains('selected')).toBe(true);
  await pointer(host.querySelector('[data-context-id="plug/core/inlet.outer"] .sketch-context-hit')!); await settle();
  expect(host.querySelector('[data-context-id="plug/core/inlet.outer"]')?.classList.contains('selected')).toBe(true);
});
it('does not solve or notify for a rim-only context revision when only the back is materialised', async () => {
  const show = async (rim: number) => act(async () => root.render(<Editor onClose={() => {}} initialDocument={documentSource} context={wooferAssemblyContext(d, rim)}/>));
  await show(8); await settle();
  await pointer(host.querySelector('[data-context-id="box.back"] .sketch-context-hit')!); await settle();
  const solve = vi.spyOn(SketchSession.prototype, 'solveFull');
  await show(9); await settle();
  expect(solve).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('Reference context changed');
});

it('shows the display-only horn wall name at the hover point, then hides it on leave', async () => {
  await render(2, 12.7);
  await act(async () => button('Fit all (Shift+F)').click());
  const wall = host.querySelector('[data-context-id="horn.wall"]')!;
  expect(wall.querySelector('text')).toBeNull();
  const vertices = [...wall.querySelector('path:not(.sketch-context-hit)')!.getAttribute('d')!.matchAll(/[ML](-?[\d.e+]+),(-?[\d.e+]+)/g)].map(m => ({ x: Number(m[1]), y: Number(m[2]) }));
  const a = vertices.at(-2)!, b = vertices.at(-1)!;
  const x = (a.x + b.x) / 2, y = (a.y + b.y) / 2;
  await pointer(svg(), 'pointermove', x, y);
  const text = wall.querySelector('text')!;
  expect(text.lastChild?.textContent).toBe('Horn wall');
  expect(text.querySelector('title')?.textContent).toBe('Horn wall');
  expect(Number(text.getAttribute('data-anchor-x'))).toBeCloseTo(x);
  expect(Number(text.getAttribute('data-anchor-y'))).toBeCloseTo(y);
  expect(Math.hypot(Number(text.getAttribute('x')) - x, Number(text.getAttribute('y')) - y)).toBeLessThanOrEqual(8);
  expect((wall as SVGElement).style.pointerEvents).toBe('none');
  await pointer(svg(), 'pointerout', 0, 0);
  expect(wall.querySelector('text')).toBeNull();
});

it('keeps throat and mouth diamonds exactly on their geometry through Fit, Fit all and Names', async () => {
  await render(2, 12.7);
  const vertices = (id: string) => [...host.querySelector(`[data-context-id="${id}"] > path:not(.sketch-context-hit)`)!.getAttribute('d')!.matchAll(/[ML](-?[\d.e+]+),(-?[\d.e+]+)/g)].map(m => ({ x: Number(m[1]), y: Number(m[2]) }));
  for (const fit of ['Fit (F)', 'Fit all (Shift+F)']) {
    await act(async () => button(fit).click());
    for (const [id, owner] of [['horn.throat', 'rim-plane'], ['horn.mouth', 'horn.wall']]) {
      const start = vertices(id)[0], end = vertices(owner).at(-1)!;
      // The diamond starts four pixels above its exact centre.
      expect(start.x).toBeCloseTo(end.x, 10);
      expect(start.y + 4).toBeCloseTo(end.y, 10);
      const path = host.querySelector(`[data-context-id="${id}"] > path:not(.sketch-context-hit)`)!.getAttribute('d');
      await act(async () => button('Names').click());
      expect(host.querySelector(`[data-context-id="${id}"] > path:not(.sketch-context-hit)`)!.getAttribute('d')).toBe(path);
      expect([...host.querySelectorAll('.sketch-context-item text')].some(t => t.textContent?.includes('◆'))).toBe(false);
    }
  }
});

it('hides colliding names in priority order: hovered item, limit, then ordinary items', async () => {
  const sketch = emptySketch();
  const first: ContextItem = { id: 'first', role: 'enclosure', kind: 'point', geometry: [{ x: .4, y: .5 }], label: 'First context item', pickable: false };
  const second: ContextItem = { ...first, id: 'second', geometry: [{ x: .5, y: .5 }], label: 'Second context item' };
  const limit: ContextItem = { ...first, id: 'limit', role: 'limit', label: 'Limit r 0.40' };
  const show = async (items: ContextItem[]) => act(async () => root.render(<SketchCanvas sketch={sketch} references={[]} problems={[]} context={{ items, rimPlaneLabel: '', fingerprint: '' }}/>));
  await show([first, second]);
  expect(host.querySelectorAll('.sketch-context-item text')).toHaveLength(0);
  await act(async () => button('Names').click());
  expect(host.querySelector('[data-context-id="first"] text')).not.toBeNull();
  expect(host.querySelector('[data-context-id="second"] text')).toBeNull();
  const move = host.querySelector('[data-context-id="second"] > path')!.getAttribute('d')!.match(/^M([^,]+),([^ ]+)/)!;
  const x = Number(move[1]), y = Number(move[2]) + 4;
  await pointer(svg(), 'pointermove', x, y);
  expect(host.querySelector('[data-context-id="second"] text')?.lastChild?.textContent).toBe(second.label);
  expect(host.querySelector('[data-context-id="first"] text')).toBeNull();
  await show([first, second, limit]);
  expect(host.querySelector('[data-context-id="limit"] text')).toBeNull();
  expect(host.querySelector('[data-context-id="second"] text')?.lastChild?.textContent).toBe(second.label);
  expect(host.querySelector('[data-context-id="first"] text')).toBeNull();
  expect(host.querySelectorAll('.sketch-context-item > path')).toHaveLength(3);
});

it('keeps dimensions ahead of colliding limit text, while retaining its exact marker', async () => {
  const state = seedPreset(documentSource, 'dome', { radius_mm: 8, height_mm: 1, surround_width_mm: 0, surround_depth_mm: 0, land_width_mm: 0 });
  const show = async (items: ContextItem[]) => act(async () => root.render(<SketchCanvas sketch={state.sketch} references={state.meta.references} problems={[]} context={{ items, rimPlaneLabel: '', fingerprint: '' }}/>));
  await show([]);
  const dimension = host.querySelector<SVGTextElement>('.sketch-dimension text')!;
  const nodes = [...host.querySelectorAll('.sketch-point[cx]')], pa = nodes[0], pb = nodes.find(p => p.getAttribute('cx') !== pa.getAttribute('cx'))!;
  const a = state.sketch.points.find(p => p.id === pa.getAttribute('data-id'))!, b = state.sketch.points.find(p => p.id === pb.getAttribute('data-id'))!;
  const scale = (Number(pb.getAttribute('cx')) - Number(pa.getAttribute('cx'))) / (b.x - a.x);
  const x = a.x + (Number(dimension.getAttribute('x')) - Number(pa.getAttribute('cx'))) / scale;
  const y = a.y - (Number(dimension.getAttribute('y')) - Number(pa.getAttribute('cy'))) / scale;
  await show([{ id: 'test.limit', role: 'limit', kind: 'point', geometry: [{ x, y }], label: 'Limit r 8.00', pickable: false }]);
  expect(host.querySelector('[data-context-id="test.limit"] text')).toBeNull();
  expect(host.querySelector('[data-context-id="test.limit"] path')).not.toBeNull();
  expect(host.querySelector('.sketch-dimension text')?.textContent).toBe(dimension.textContent);
  await act(async () => button('Dimensions').click());
  expect(host.querySelector('[data-context-id="test.limit"] text')).not.toBeNull();
});

it('shows a selected enclosure name with Names off', async () => {
  await render();
  await act(async () => button('Fit all (Shift+F)').click());
  expect(host.querySelector('[data-context-id="box.back"] text')).toBeNull();
  await pointer(host.querySelector('[data-context-id="box.back"] .sketch-context-hit')!); await settle();
  expect(button('Names').getAttribute('aria-pressed')).toBe('false');
  expect(host.querySelector('[data-context-id="box.back"] text')?.lastChild?.textContent).toBe('Enclosure back');
});


it.each([true, false])('shows the mouth name over the keep-out band on its actual marker (pickable: %s)', async pickable => {
  const c = hornAssemblyContext({ ...d, mouth_radius_mm: 140 }, [], undefined, 12.7);
  c.items = c.items.map(item => item.id === 'horn.mouth' ? { ...item, pickable } : item);
  await act(async () => root.render(<SketchCanvas sketch={emptySketch()} references={[]} problems={[]} context={c}/>));
  await act(async () => button('Fit all (Shift+F)').click());
  expect(button('Names').getAttribute('aria-pressed')).toBe('false');
  const mouth = host.querySelector('[data-context-id="horn.mouth"]')!;
  const hit = mouth.querySelector(pickable ? '.sketch-context-hit' : 'path')!;
  const start = hit.getAttribute('d')!.match(/^M([^,]+),([^ ]+)/)!;
  // Slightly off the centre: the band edge is closer than the marker.
  const x = Number(start[1]), y = Number(start[2]) + 5;
  await pointer(hit, 'pointermove', x, y);
  expect(mouth.classList.contains('hovered')).toBe(true);
  const label = mouth.querySelector('text')!;
  expect(label?.lastChild?.textContent).toBe('Mouth r 140.00');
  expect(Math.hypot(Number(label.getAttribute('x')) - x, Number(label.getAttribute('y')) - (y - 1))).toBeLessThanOrEqual(8);
  expect(host.querySelector('.role-keepout.hovered')).toBeNull();
});

it('renders the nearest side-wall hover name from its hit path with Names off', async () => {
  const c = hornAssemblyContext({ ...d, width_mm: 500, height_mm: 600, depth_mm: 240, horn_length_mm: 140, mouth_radius_mm: 140 }, [], undefined, 12.7);
  await act(async () => root.render(<Editor onClose={() => {}} initialDocument={documentSource} limit={{ kind: 'throat', radius: 12.7, hard: true }} context={c}/>)); await settle();
  await act(async () => button('Fit all (Shift+F)').click());
  expect(button('Names').getAttribute('aria-pressed')).toBe('false');
  const wall = host.querySelector('[data-context-id="box.side.near"]')!;
  const hit = wall.querySelector('.sketch-context-hit')!;
  const vertices = [...hit.getAttribute('d')!.matchAll(/[ML](-?[\d.e+]+),(-?[\d.e+]+)/g)].map(m => ({ x: Number(m[1]), y: Number(m[2]) }));
  const x = vertices[0].x, y = (vertices[0].y + vertices[1].y) / 2;
  expect(wall.querySelector('text')).toBeNull();
  await pointer(hit, 'pointermove', x, y);
  expect(wall.classList.contains('hovered')).toBe(true);
  const label = wall.querySelector('text')!;
  expect(label?.lastChild?.textContent).toBe(c.items.find(item => item.id === 'box.side.near')!.label);
  expect(Math.hypot(Number(label.getAttribute('x')) - x, Number(label.getAttribute('y')) - y)).toBeLessThanOrEqual(8);
  expect(Number(label.getAttribute('data-anchor-x'))).toBeCloseTo(x);
  expect(Number(label.getAttribute('data-anchor-y'))).toBeCloseTo(y);
});
