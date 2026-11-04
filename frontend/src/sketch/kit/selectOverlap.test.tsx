import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { importedSketch } from '../ops';
import type { Sketch } from '../model';
import { sourceDisplay } from '../adapters/sourceDisplay';
import { sketchViewKey } from '../viewPreferences';
import type { SketchWorkspaceSession } from '../workspace/sketchSession';
import { SketchKit } from './sketchKit';

let host: HTMLDivElement, root: Root;
const changed = vi.fn();
const base = () => importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }],
  [{ id: 'wall', kind: 'line', p1: 'p', p2: 'q', data: { name: 'Throat wall' } }]);
const session: SketchWorkspaceSession = {
  command: vi.fn(), constraint: vi.fn(), dimension: vi.fn(), beginDrag: vi.fn(), dragTo: vi.fn(), endDrag: vi.fn(), undo: vi.fn(),
};
const presentation = {
  axes: { x: { label: 'x', unit: 'mm' as const }, y: { label: 'y', unit: 'mm' as const } },
  display: (sketch: Sketch, ui: Parameters<typeof sourceDisplay>[1]) => sourceDisplay({ sketch, meta: { references: [] } }, ui),
  initialView: () => ({ scale: 10, offsetX: 100, offsetY: 200 }),
};
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.removeItem(sketchViewKey); vi.clearAllMocks();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
const svg = () => host.querySelector<SVGSVGElement>('svg[role="application"]')!;
const rows = () => [...document.querySelectorAll<HTMLButtonElement>('[aria-label="Select other"] [role="menuitem"]')];
const event = async (target: Element, type: string, x: number, y: number, options = {}) => act(async () => {
  target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, buttons: type === 'pointerup' ? 0 : 1, ...options }));
});
const key = async (key: string, options = {}, target: Element = document.activeElement!) => act(async () => {
  target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options }));
});
const open = async (x = 100, y = 200) => event(svg(), 'contextmenu', x, y, { button: 2 });
async function mount(sketch = base()) {
  await act(async () => root.render(<div role="dialog" aria-label="Editor"><SketchKit presentation={presentation} sketch={sketch} session={session} references={[]} problems={[]} onSelectionChange={changed}/></div>));
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
}
const selected = () => changed.mock.lastCall?.[0];

it('opens point, nearest curves, then dimension in the same order as cycling, with names and glyphs', async () => {
  const sketch = base();
  sketch.points.push({ id: 'a', x: 0, y: .5 }, { id: 'b', x: 10, y: .5 });
  sketch.entities.push({ id: 'near', kind: 'line', p1: 'a', p2: 'b', data: { name: 'Near wall' } });
  sketch.constraints.push({ id: 'length', kind: 'distance', a: 'p', b: 'q', value: 10, driving: true, placement: { offset: 0, along: 0 } });
  await mount(sketch); await open();
  expect(rows().map(row => row.textContent)).toEqual(['•PointPoint 1', '•PointPoint 3', '╱LineThroat wall', '╱LineNear wall', '↔DimensionDistance 10.00 mm']);
  expect(document.activeElement).toBe(rows()[0]);
  expect(rows()[0].closest('[role="dialog"]')?.getAttribute('aria-label')).toBe('Editor');
  expect(rows().every(row => row.querySelector('[aria-hidden="true"]'))).toBe(true);
});
it('highlights the hovered curve and point using the canvas hover classes and clears on dismissal', async () => {
  await mount(); await open();
  await event(rows()[1], 'pointerover', 100, 200);
  expect(host.querySelector('.sketch-entity[data-id="wall"]')?.classList.contains('hovered')).toBe(true);
  await event(rows()[0], 'pointerover', 100, 200);
  expect(host.querySelector('.sketch-point[data-id="p"]')?.classList.contains('hovered')).toBe(true);
  await key('Escape');
  expect(rows()).toHaveLength(0);
  expect(host.querySelectorAll('.hovered')).toHaveLength(0);
  expect(document.activeElement).toBe(svg());
});
it.each([{ shiftKey: true }, { ctrlKey: true }, { metaKey: true }, {}])('selects menu rows with modifiers %j', async options => {
  await mount(); await open(); await event(rows()[0], 'click', 100, 200);
  expect(selected()).toEqual(['p']);
  await open(); await event(rows()[1], 'click', 100, 200, options);
  expect(selected()).toEqual(Object.keys(options).length ? ['p', 'wall'] : ['wall']);
  expect(rows()).toHaveLength(0);
  if ('ctrlKey' in options || 'metaKey' in options) {
    await open(); await event(rows()[1], 'click', 100, 200, options);
    expect(selected()).toEqual(['p']);
  }
});
it('moves with arrows, wraps, and selects with Enter including modifiers', async () => {
  await mount(); await open();
  await key('ArrowUp'); expect(document.activeElement).toBe(rows()[1]);
  expect(host.querySelector('.sketch-entity.hovered')?.getAttribute('data-id')).toBe('wall');
  await key('ArrowDown'); expect(document.activeElement).toBe(rows()[0]);
  await key('Enter'); expect(selected()).toEqual(['p']);
  await open(); await key('ArrowDown'); await key('Enter', { shiftKey: true });
  expect(selected()).toEqual(['p', 'wall']); expect(rows()).toHaveLength(0);
});
it('closes on an outside pointerdown and Escape on BODY without changing the selection', async () => {
  await mount(); await open(); await event(rows()[0], 'click', 100, 200);
  await open(); await event(document.body, 'pointerdown', 0, 0);
  expect(rows()).toHaveLength(0); expect(selected()).toEqual(['p']);
  await open(); await key('Escape', {}, document.body);
  expect(rows()).toHaveLength(0); expect(selected()).toEqual(['p']);
});
it('selects a single hit, ignores empty space, and suppresses context menus only inside the canvas', async () => {
  await mount(); await open(150, 200);
  expect(rows()).toHaveLength(0); expect(selected()).toEqual(['wall']);
  await open(500, 300); expect(rows()).toHaveLength(0); expect(selected()).toEqual(['wall']);
  const canvasEvent = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  await act(async () => { svg().dispatchEvent(canvasEvent); }); expect(canvasEvent.defaultPrevented).toBe(true);
  const outsideEvent = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  host.dispatchEvent(outsideEvent); expect(outsideEvent.defaultPrevented).toBe(false);
});
it('opens only in Select and preserves repeated-click cycling with the new hint', async () => {
  await mount();
  for (const [index, id] of [[1, 'p'], [2, 'wall']] as const) {
    await event(svg(), 'pointerdown', 100, 200); await event(svg(), 'pointerup', 100, 200);
    expect(selected()).toEqual([id]); expect(host.querySelector('.sketch-status')?.textContent).toContain(`${index} of 2 · right-click to choose`);
  }
  await key('l', {}, svg()); await open(); expect(rows()).toHaveLength(0);
});
async function marquee(x1: number, y1: number, x2: number, y2: number, options = {}) {
  await event(svg(), 'pointerdown', x1, y1, options); await event(svg(), 'pointermove', x2, y2, options); await event(svg(), 'pointerup', x2, y2, options);
}
function dimensionSketch(kind: 'distanceX' | 'distanceY') {
  const sketch = kind === 'distanceX' ? base() : importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 0, y: 10 }], [{ id: 'wall', kind: 'line', p1: 'p', p2: 'q' }]);
  sketch.constraints.push({ id: 'length', kind, a: 'p', b: 'q', value: 10, driving: true, placement: { offset: 5, along: .5 } });
  return sketch;
}
function labelBounds() {
  const group = host.querySelector('[data-constraint-id="length"]')!;
  const rect = group.querySelector('rect')!, text = group.querySelector('text')!;
  const x = Number(text.getAttribute('x')), y = Number(text.getAttribute('y'));
  const width = Number(rect.getAttribute('width')), height = Number(rect.getAttribute('height'));
  return group.querySelector('[transform]') ? { left: x - 17, top: y - width / 2, width: height, height: width }
    : { left: Number(rect.getAttribute('x')), top: Number(rect.getAttribute('y')), width, height };
}
it.each(['distanceX', 'distanceY'] as const)('marquees %s label bounds with window, crossing, replace, add and toggle', async kind => {
  await mount(dimensionSketch(kind));
  let b = labelBounds();
  await marquee(b.left - 10, b.top - 10, b.left + b.width + 10, b.top + b.height + 10);
  expect(selected()).toContain('length');
  // A partial window must drop the label; a crossing box touching the label includes it.
  await marquee(b.left - 10, b.top - 10, b.left + 2, b.top + 2);
  expect(selected()).not.toContain('length');
  b = labelBounds();
  await marquee(b.left + 2, b.top - 10, b.left - 10, b.top + 2);
  expect(selected()).toContain('length');
  await marquee(80, 180, 120, 220, { shiftKey: true });
  expect(selected()).toContain('p'); expect(selected()).toContain('length');
  b = labelBounds();
  await marquee(b.left - 10, b.top - 10, b.left + b.width + 10, b.top + b.height + 10, { ctrlKey: true });
  expect(selected()).toContain('p'); expect(selected()).not.toContain('length');
});
it('does not marquee hidden dimension labels', async () => {
  await mount(dimensionSketch('distanceX'));
  const button = [...host.querySelectorAll('button')].find(b => b.textContent === 'Dimensions')!;
  await act(async () => button.click());
  expect(host.querySelector('[data-constraint-id="length"]')).toBeNull();
  await marquee(20, 20, 300, 300); expect(selected()).not.toContain('length');
});
it('Escape cancels an empty-selection marquee and consumes it before an enclosing dialog', async () => {
  await mount(); const enclosing = vi.fn(); document.addEventListener('keydown', enclosing);
  try {
    await event(svg(), 'pointerdown', 40, 40); await event(svg(), 'pointermove', 240, 240);
    expect(host.querySelector('.sketch-marquee.window')).not.toBeNull();
    await key('Escape', {}, document.body);
    expect(host.querySelector('.sketch-marquee')).toBeNull(); expect(enclosing).not.toHaveBeenCalled();
    await event(svg(), 'pointerup', 240, 240); expect(selected()).toEqual([]);
  } finally { document.removeEventListener('keydown', enclosing); }
});

it('Escape restores the selection from before a replacing marquee', async () => {
  await mount(); await open(); await event(rows()[0], 'click', 100, 200);
  expect(selected()).toEqual(['p']);
  await event(svg(), 'pointerdown', 40, 40); await event(svg(), 'pointermove', 240, 240);
  expect(selected()).toEqual([]);
  await key('Escape', {}, document.body); await event(svg(), 'pointerup', 240, 240);
  expect(selected()).toEqual(['p']); expect(host.querySelector('.sketch-marquee')).toBeNull();
});
it('previews and chooses an overlapping dimension without editing geometry or creating history', async () => {
  const sketch = base();
  sketch.constraints.push({ id: 'length', kind: 'distance', a: 'p', b: 'q', value: 10, driving: true, placement: { offset: 0, along: .5 } });
  await mount(sketch); await open(150, 200);
  expect(rows()).toHaveLength(2);
  await event(rows()[1], 'pointerover', 150, 200);
  expect(host.querySelector('[data-id="length"].sketch-glyph.selected')).not.toBeNull();
  await key('Enter'); expect(selected()).toEqual(['length']);
  expect(session.command).not.toHaveBeenCalled(); expect(session.dimension).not.toHaveBeenCalled();
});
