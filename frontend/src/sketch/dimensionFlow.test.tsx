import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useModalDialogFocus } from '../shell/dialogFocus';
import { SketchCanvas } from './SketchCanvas';
import { SketchController } from './controller';
import type { Reference } from './adapter';
import { importedSketch, applyCommand } from './ops';
import type { Sketch } from './model';
import type { Diagnosis } from './session';
import type { XY } from './solver/types';
import { realSessionReady } from './testSupport';
import { sketchViewKey } from './viewPreferences';
import { arcGeometry } from './geometry';
import { seed, lower } from './adapters/sourceContour';
import { presetDefaults } from './adapters/sourcePreset';
import { expandedPreset } from './adapters/presetTestSupport';

const references: Reference[] = [{ id: 'axis', kind: 'axis' }, { id: 'baffle', kind: 'baffle' }];
let host: HTMLDivElement, root: Root, current: Sketch;
const closeDialog = vi.fn();
let controller: SketchController<null>;
function Harness({ initial }: { initial: Sketch }) {
  const dialog = useModalDialogFocus<HTMLDivElement>({ open: true, onClose: closeDialog });
  const [sketch, setSketch] = useState(initial), [diagnosis, setDiagnosis] = useState<Diagnosis>();
  const [session, setSession] = useState<SketchController<null>>();
  useEffect(() => {
    const c = new SketchController({ sketch: initial, meta: null }, references, (s, command) => {
      const result = applyCommand(s.sketch, command);
      return 'error' in result ? result : { ...s, sketch: result.sketch };
    }, (s, d) => { current = s.sketch; setSketch(s.sketch); setDiagnosis(d); setSession(c); });
    controller = c;
    return () => c.destroy();
  }, [initial]);
  return <div ref={dialog} role="dialog"><SketchCanvas sketch={sketch} diagnosis={diagnosis} references={references} problems={[]} session={session}/></div>;
}
beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  closeDialog.mockClear();
  localStorage.removeItem(sketchViewKey); await realSessionReady();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
const svg = () => host.querySelector<SVGSVGElement>('[role="application"]')!;
const field = () => host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]');
const button = (label: string) => [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') ?? b.textContent) === label)!;
const pointer = async (type: string, at: XY, target: Element = svg()) => act(async () => {
  target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: at.x, clientY: at.y, buttons: type === 'pointerup' ? 0 : 1 }));
});
const key = async (key: string, target: Element = svg(), options = {}) => act(async () => { target.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key, ...options })); });
async function input(value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field(), value);
    field()!.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
function screen(at: XY): XY {
  const points = current.points.filter(p => host.querySelector(`circle.sketch-point[data-id="${p.id}"]`));
  if (points.length < 2) return { x: 120 + at.x * 14.8, y: 170 - at.y * 14.8 };
  const a = points[0], b = points.find(p => p.x !== a.x)!, c = host.querySelector(`[data-id="${a.id}"]`)!, d = host.querySelector(`[data-id="${b.id}"]`)!;
  const scale = (Number(d.getAttribute('cx')) - Number(c.getAttribute('cx'))) / (b.x - a.x);
  return { x: Number(c.getAttribute('cx')) + (at.x - a.x) * scale, y: Number(c.getAttribute('cy')) - (at.y - a.y) * scale };
}
async function pick(id: string) {
  const p = current.points.find(p => p.id === id);
  const e = current.entities.find(e => e.id === id);
  let at: XY;
  if (p) at = p;
  else if (id === 'axis') at = { x: 0, y: 8 };
  else if (id === 'baffle') at = { x: 8, y: 0 };
  else if (e?.kind === 'line') {
    const a = current.points.find(p => p.id === e.p1)!, b = current.points.find(p => p.id === e.p2)!;
    at = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  } else if (e?.kind === 'arc') {
    const g = arcGeometry(current, e), theta = g.start + g.sweep / 2;
    at = { x: g.center.x + g.radius * Math.cos(theta), y: g.center.y + g.radius * Math.sin(theta) };
  } else throw new Error(`Unknown pick ${id}`);
  const atScreen = screen(at);
  await pointer('pointermove', atScreen);
  if (p) expect(host.querySelector(`.sketch-point[data-id="${id}"]`)!.classList.contains('hovered')).toBe(true);
  else if (references.some(r => r.id === id)) expect(host.querySelector(`[data-reference-id="${id}"]`)!.classList.contains('hovered')).toBe(true);
  else expect(host.querySelector(`.sketch-entity[data-id="${id}"]`)!.classList.contains('hovered')).toBe(true);
  await pointer('pointerdown', atScreen); await pointer('pointerup', atScreen);
}
async function mount(initial: Sketch) {
  current = initial; await act(async () => root.render(<Harness initial={initial}/>));
  // Finish the modal's initial autofocus before simulating the user's tool selection.
  await act(async () => { await new Promise<void>(resolve => requestAnimationFrame(() => resolve())); });
  await key('d');
}
function fixture(kind: string) {
  let sketch = importedSketch([
    { id: 'p', x: 4, y: 4 }, { id: 'q', x: 14, y: kind === 'angle' || kind === 'pointLine' || kind === 'parallel' ? 4 : 10 },
    { id: 'r', x: kind === 'angle' ? 14 : 8, y: 14 }, { id: 's', x: 18, y: kind === 'parallel' ? 14 : 18 },
    { id: 'origin', x: 0, y: 0 }, { id: 'up', x: 0, y: 1 }, { id: 'right', x: 1, y: 0 },
  ], [
    { kind: 'line', id: 'line', p1: 'p', p2: 'q' },
    { kind: 'line', id: 'other', p1: kind === 'angle' ? 'q' : 'r', p2: kind === 'angle' ? 'r' : 's' },
    { kind: 'line', id: 'axis', p1: 'origin', p2: 'up', reference: true, construction: true },
    { kind: 'line', id: 'baffle', p1: 'origin', p2: 'right', reference: true, construction: true },
  ]);
  if (kind === 'arc') sketch.entities.push({ kind: 'arc', id: 'arc', center: 'r', start: 'q', end: 's', ccw: true });
  if (kind === 'arc') {
    sketch.points = sketch.points.map(p => p.id === 'q' ? { ...p, x: 13, y: 14 } : p.id === 's' ? { ...p, x: 8, y: 19 } : p);
  }
  const fixes = kind === 'pointLine' || kind === 'angle' || kind === 'parallel' ? ['p', 'q'] : [kind === 'arc' ? 'r' : 'p'];
  for (const point of fixes) {
    const result = applyCommand(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point } });
    if ('error' in result) throw new Error(result.error); sketch = result.sketch;
  }
  if (kind === 'parallel') {
    const result = applyCommand(sketch, { kind: 'addConstraint', constraint: { kind: 'parallel', a: 'line', b: 'other' } });
    if ('error' in result) throw new Error(result.error); sketch = result.sketch;
  }
  return sketch;
}
async function previewPlace(at: XY, kind: string) {
  const before = current;
  await pointer('pointermove', screen(at));
  const preview = host.querySelector('.sketch-dimension-preview')!;
  expect(preview.getAttribute('aria-label')).toBe(`Preview ${kind}`);
  expect(preview.textContent).toMatch(/\d+\.\d{2}/);
  expect(field()).toBeNull(); expect(current).toBe(before);
  const text = preview.querySelector('text')!, x = Number(text.getAttribute('x')), y = Number(text.getAttribute('y'));
  await pointer('pointerdown', screen(at)); await pointer('pointerup', screen(at));
  expect(field()).not.toBeNull(); expect(document.activeElement).toBe(field());
  expect(field()!.selectionStart).toBe(0); expect(field()!.selectionEnd).toBe(field()!.value.length);
  expect(current.constraints.at(-1)!.placement).toBeDefined();
  // Labels and the editor open at the chosen position, subject to edge clamping.
  const form = host.querySelector<HTMLElement>('[aria-label="Edit dimension"]')!;
  expect(parseFloat(form.style.left)).toBeCloseTo(Math.max(4, Math.min(x, 460)));
  expect(parseFloat(form.style.top)).toBeCloseTo(Math.max(4, Math.min(y, 260)));
}

describe('dimension pick → preview → place → type with the real solver', () => {
  it.each([
    ['line', ['line'], { x: -4, y: 16 }, 'distance', 14],
    ['points', ['p', 'q'], { x: 0, y: 16 }, 'distance', 14],
    ['axis', ['q', 'axis'], { x: 6, y: 16 }, 'distanceX', 16],
    ['baffle', ['q', 'baffle'], { x: 20, y: 6 }, 'distanceY', 12],
    ['angle', ['line', 'other'], { x: 18, y: 9 }, 'angle', 70],
    ['arc', ['arc'], { x: 15, y: 21 }, 'radius', 7],
    ['pointLine', ['r', 'line'], { x: 20, y: 8 }, 'pointLineDistance', 12],
    ['parallel', ['line', 'other'], { x: 20, y: 8 }, 'pointLineDistance', 12],
  ] as const)('%s changes geometry and undoes add plus edit together', async (name, ids, at, kind, value) => {
    const initial = fixture(name); await mount(initial);
    expect(host.querySelector('[aria-label="Dimension measurement"]')).toBeNull();
    for (const id of ids) await pick(id);
    expect(current.constraints).toHaveLength(initial.constraints.length);
    await previewPlace(at, kind);
    const measured = current;
    await input(String(value));
    expect(current).toBe(measured); // Typing is never a solve.
    await key('Enter', field()!);
    expect(field()).toBeNull(); expect(current.points).not.toEqual(measured.points);
    const dimension = current.constraints.at(-1)!;
    expect(dimension).toMatchObject({ kind, value: kind === 'angle' ? value * Math.PI / 180 : value, driving: true });
    if (name === 'axis' || name === 'baffle') {
      expect(dimension.label).toBe(name === 'axis' ? 'r' : 'z');
      expect(host.querySelector(`[data-constraint-id="${dimension.id}"] text`)!.textContent).toContain(dimension.label);
      for (const id of ['origin', 'up', 'right']) expect(current.points.find(p => p.id === id)).toEqual(initial.points.find(p => p.id === id));
    }
    expect(button('Dimension (D)').getAttribute('aria-pressed')).toBe('true');
    await key('z', svg(), { ctrlKey: true }); expect(current).toEqual(initial);
    await key('z', svg(), { ctrlKey: true, shiftKey: true }); expect(current.constraints.at(-1)).toEqual(dimension);
  });

  it('cancels a pick before leaving the tool on the second Esc', async () => {
    await mount(fixture('line')); await pick('line');
    await pointer('pointermove', screen({ x: -4, y: 16 }));
    expect(host.querySelector('.sketch-dimension-preview')).not.toBeNull();
    await key('Escape'); expect(host.querySelector('.sketch-dimension-preview')).toBeNull();
    expect(button('Dimension (D)').getAttribute('aria-pressed')).toBe('true');
    await key('Escape'); expect(button('Select (Esc)').getAttribute('aria-pressed')).toBe('true');
  });
  it.each(['Escape', 'outside'] as const)('%s keeps the measured value and one undo step', async how => {
    const initial = fixture('line'); await mount(initial); await pick('line'); await previewPlace({ x: -4, y: 16 }, 'distance');
    const measured = current; await input('100');
    if (how === 'Escape') await key('Escape', field()!);
    else await pointer('pointerdown', { x: 600, y: 300 }, document.body);
    expect(field()).toBeNull(); expect(current).toBe(measured);
    expect(button('Dimension (D)').getAttribute('aria-pressed')).toBe('true');
    await key('z', svg(), { ctrlKey: true }); expect(current).toEqual(initial);
  });
  it('opens a selected label with Enter and applies only on Enter, with refusal text selected', async () => {
    await mount(fixture('arc')); await pick('arc'); await previewPlace({ x: 15, y: 21 }, 'radius');
    await key('Escape', field()!); await key('Escape');
    const label = host.querySelector('[data-constraint-id] text[aria-label^="Dimension"]')!;
    await pointer('pointerdown', { x: 500, y: 100 }, label); await pointer('pointerup', { x: 500, y: 100 });
    await key('Enter'); expect(document.activeElement).toBe(field());
    const before = current; await input('-1'); expect(current).toBe(before); await key('Enter', field()!);
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('negative');
    expect(document.activeElement).toBe(field()); expect(field()!.selectionEnd).toBe(2);
    await input('6'); await key('Enter', field()!); expect(field()).toBeNull();
    await key('z', svg(), { ctrlKey: true }); expect(current).toEqual(before); // An existing-value edit has its own undo.
  });
  it('keeps a refused continuation open, restores geometry and permits a corrected retry in the same undo', async () => {
    let initial = fixture('line');
    // The fixed endpoint and driven length put q on a circle. A vertical distance
    // greater than its radius cannot be reached by continuation.
    const held = applyCommand(initial, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'p', b: 'q', value: Math.hypot(10, 6), driving: true } });
    if ('error' in held) throw new Error(held.error); initial = held.sketch;
    await mount(initial); await pick('p'); await pick('q'); await previewPlace({ x: 20, y: 7 }, 'distanceY');
    const measured = current; await input('20'); await key('Enter', field()!);
    expect(current).toBe(measured);
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('cannot be solved');
    expect(document.activeElement).toBe(field()); expect(field()!.selectionStart).toBe(0); expect(field()!.selectionEnd).toBe(2);
    await input('7'); await key('Enter', field()!); expect(field()).toBeNull();
    expect(current.points.find(p => p.id === 'q')!.y - current.points.find(p => p.id === 'p')!.y).toBeCloseTo(7);
    await key('z', svg(), { ctrlKey: true }); expect(current).toEqual(initial);
  });
  it.each(['reference', 'cancel'] as const)('asks before an over-constraint: %s', async choice => {
    await mount(fixture('line')); await pick('line'); await previewPlace({ x: -4, y: 16 }, 'distance');
    await key('Escape', field()!); const measured = current;
    await pick('line'); await pointer('pointermove', screen({ x: -4, y: 18 })); await pointer('pointerdown', screen({ x: -4, y: 18 })); await pointer('pointerup', screen({ x: -4, y: 18 }));
    expect(host.querySelector('[aria-label="Over-constrained dimension"]')!.textContent).toContain('This dimension would over-constrain the sketch.');
    expect(current).toBe(measured); expect(field()).toBeNull();
    await act(async () => button(choice === 'reference' ? 'Make it a reference (driven) dimension' : 'Cancel').click());
    expect(host.querySelector('[aria-label="Over-constrained dimension"]')).toBeNull();
    if (choice === 'cancel') expect(current).toBe(measured);
    else {
      expect(current.constraints.at(-1)).toMatchObject({ driving: false, placement: expect.any(Object) });
      await key('z', svg(), { ctrlKey: true }); expect(current).toEqual(measured);
    }
  });
});


describe('dimension interaction regressions', () => {
  it('waits for mouse release and retains focus through the full browser gesture before applying 12', async () => {
    await mount(fixture('line')); await pick('line');
    let captured = false;
    svg().setPointerCapture = () => { captured = true; };
    svg().hasPointerCapture = () => captured;
    svg().releasePointerCapture = () => { captured = false; };
    const at = screen({ x: -4, y: 16 });
    await pointer('pointermove', at);
    await pointer('pointerdown', at);
    expect(field()).toBeNull();
    act(() => svg().focus());
    expect(captured).toBe(true);
    await pointer('pointerup', at);
    expect(captured).toBe(false);
    await pointer('click', at);
    expect(document.activeElement).toBe(field());
    expect(field()!.selectionEnd).toBe(field()!.value.length);
    // Focus churn alone is not an outside interaction.
    act(() => svg().focus());
    expect(field()).not.toBeNull();
    act(() => field()!.focus());
    await input('12'); await key('Enter', field()!);
    expect(current.constraints.at(-1)).toMatchObject({ kind: 'distance', value: 12 });
  });
  it('cancels Dimension with body focus without closing its editor dialog', async () => {
    await mount(fixture('line')); act(() => svg().blur());
    expect(document.activeElement).toBe(document.body);
    await key('Escape', document.body);
    expect(closeDialog).not.toHaveBeenCalled();
    expect(button('Select (Esc)').getAttribute('aria-pressed')).toBe('true');
    act(() => svg().blur()); await key('Escape', document.body);
    expect(closeDialog).toHaveBeenCalledOnce();
  });
  it('places a completed two-point pick on a third entity without highlighting it as another pick', async () => {
    await mount(fixture('line')); await pick('p'); await pick('q');
    const at = screen({ x: 13, y: 16 }); // midpoint of other
    await pointer('pointermove', at);
    expect(host.querySelector('.sketch-entity[data-id="other"]')!.classList.contains('hovered')).toBe(false);
    expect(host.querySelector('.sketch-dimension-preview')).not.toBeNull();
    await pointer('pointerdown', at); await pointer('pointerup', at);
    expect(field()).not.toBeNull();
    expect(current.constraints.at(-1)).toMatchObject({ a: 'p', b: 'q' });
  });
  it('shows placement on an unsupported second entity and places a single arc there', async () => {
    await mount(fixture('arc')); await pick('arc');
    const at = screen({ x: 8, y: 16.5 });
    await pointer('pointermove', at);
    expect(host.querySelector('.sketch-entity[data-id="other"]')!.classList.contains('hovered')).toBe(false);
    expect(host.querySelector('.sketch-dimension-preview')!.getAttribute('aria-label')).toBe('Preview radius');
    await pointer('pointerdown', at); await pointer('pointerup', at);
    expect(current.constraints.at(-1)).toMatchObject({ kind: 'radius' });
  });
  it('highlights a valid second line as a pick and hides placement until that pick completes', async () => {
    await mount(fixture('angle')); await pick('line');
    const at = screen({ x: 14, y: 9 }); await pointer('pointermove', at);
    expect(host.querySelector('.sketch-entity[data-id="other"]')!.classList.contains('hovered')).toBe(true);
    expect(host.querySelector('.sketch-dimension-preview')).toBeNull();
    await pointer('pointerdown', at); await pointer('pointerup', at);
    expect(field()).toBeNull();
    await previewPlace({ x: 20, y: 10 }, 'angle');
  });
  it('uses the click dismissing a value box as the first pick of the next dimension', async () => {
    await mount(fixture('line')); await pick('line'); await previewPlace({ x: -4, y: 16 }, 'distance');
    const at = screen(current.points.find(p => p.id === 'r')!);
    await pointer('pointermove', at); await pointer('pointerdown', at); await pointer('pointerup', at);
    expect(field()).toBeNull();
    expect(host.querySelector('.sketch-point[data-id="r"]')!.classList.contains('selected')).toBe(true);
    await pick('s');
    await previewPlace({ x: 22, y: 24 }, 'distance');
    expect(current.constraints.at(-1)).toMatchObject({ a: 'r', b: 's' });
  });
  it.each(['Delete selection', 'Reset position', 'Undo'])('leaves Enter on %s to the focused button', async label => {
    await mount(fixture('arc')); await pick('arc'); await previewPlace({ x: 15, y: 21 }, 'radius');
    await key('Escape', field()!); await key('Escape');
    const dimension = host.querySelector('[data-constraint-id] text[aria-label^="Dimension"]')!;
    await pointer('pointerdown', { x: 500, y: 100 }, dimension); await pointer('pointerup', { x: 500, y: 100 });
    const target = button(label); act(() => target.focus());
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter' });
    await act(async () => { target.dispatchEvent(event); });
    expect(field()).toBeNull(); expect(event.defaultPrevented).toBe(false);
    await act(async () => target.click());
    if (label === 'Delete selection' || label === 'Undo') expect(current.constraints.some(c => 'value' in c)).toBe(false);
    else expect(current.constraints.at(-1)!.placement).toBeUndefined();
  });
  it.each(['undo', 'redo', 'external'] as const)('clears pending picks after %s changes the sketch', async how => {
    await mount(fixture('line')); await pick('line'); await previewPlace({ x: -4, y: 16 }, 'distance');
    await key('Escape', field()!);
    if (how === 'redo') await key('z', svg(), { ctrlKey: true });
    await pick('r'); await pick('s'); await pointer('pointermove', screen({ x: 22, y: 24 }));
    expect(host.querySelector('.sketch-dimension-preview')).not.toBeNull();
    if (how === 'external') await act(async () => { await controller.command({ kind: 'setConstruction', id: 'other', construction: true }); });
    else await key('z', svg(), { ctrlKey: true, shiftKey: how === 'redo' });
    expect(host.querySelector('.sketch-dimension-preview')).toBeNull();
  });
  it('dismisses on Tab blur while ignoring incidental opening focus churn', async () => {
    await mount(fixture('line')); await pick('line'); await previewPlace({ x: -4, y: 16 }, 'distance');
    const measured = current;
    act(() => svg().focus()); expect(field()).not.toBeNull();
    act(() => field()!.focus()); await key('Tab', field()!);
    act(() => button('Apply dimension value').focus());
    expect(field()).toBeNull(); expect(current).toBe(measured);
  });
  it.each(['Escape', 'tool change', 'overconstraint'])('does not reopen a box or prompt after %s during the add solve', async how => {
    await mount(fixture('line')); await pick('line');
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const original = controller.constraint.bind(controller);
    vi.spyOn(controller, 'constraint').mockImplementation(async (...args) => {
      await gate;
      if (how === 'overconstraint') throw { overconstrained: true };
      return original(...args);
    });
    const at = screen({ x: -4, y: 16 });
    await pointer('pointerdown', at); await pointer('pointerup', at);
    if (how === 'tool change') await key('l'); else await key('Escape');
    await act(async () => { release(); });
    expect(field()).toBeNull();
    expect(host.querySelector('[aria-label="Over-constrained dimension"]')).toBeNull();
  });
});

describe('hand-drawn cone dimensions', () => {
  it('recreates the preset radius, depth and surround width using drawing and dimension tools', async () => {
    const document = expandedPreset('cone', { radius_mm: 9, depth_mm: 3.5, surround_width_mm: 1.6 });
    const empty = seed({ ...document, contour: { ...document.contour, points: [], segments: [] } });
    await mount(empty.sketch); await key('Escape');
    // Give the blank sketch a useful mm scale, then draw only through canvas gestures.
    await act(async () => {
      svg().dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 1, clientX: 172, clientY: 328 }));
      svg().dispatchEvent(new MouseEvent('pointermove', { bubbles: true, buttons: 4, clientX: 120, clientY: 170 }));
      svg().dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
    });
    await act(async () => { svg().dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, clientX: 120, clientY: 170, deltaY: Math.log(20) * 1000 })); });
    const clickWorld = async (at: XY) => { const pos = screen(at); await pointer('pointermove', pos); await pointer('pointerdown', pos); await pointer('pointerup', pos); };
    const pole = document.contour.points[0], cap = document.contour.points[1], cone = document.contour.points[2], surround = document.contour.points[3], rim = document.contour.points[4];
    const capCenter = document.contour.segments[0].center_mm!;
    const theta = (Math.PI / 2 + Math.atan2(cap.z_mm - capCenter[1], cap.r_mm)) / 2;
    const capRadius = Math.hypot(cap.r_mm, cap.z_mm - capCenter[1]);
    await key('a');
    await clickWorld({ x: pole.r_mm, y: pole.z_mm });
    await clickWorld({ x: cap.r_mm, y: cap.z_mm });
    await clickWorld({ x: capCenter[0] + capRadius * Math.cos(theta), y: capCenter[1] + capRadius * Math.sin(theta) });
    const capArc = current.entities.find(e => e.kind === 'arc')!;
    if (capArc.kind !== 'arc') throw new Error('Expected drawn cap');
    const capId = capArc.end;
    await key('l'); await clickWorld({ x: cap.r_mm, y: cap.z_mm }); await clickWorld({ x: cone.r_mm, y: 0 });
    const coneLine = current.entities.find(e => e.kind === 'line' && !e.construction)!;
    if (coneLine.kind !== 'line') throw new Error('Expected drawn cone');
    const coneId = coneLine.p2;
    await key('a'); // The chain end is already the first arc pick.
    await clickWorld({ x: surround.r_mm, y: 0 });
    await clickWorld({ x: (cone.r_mm + surround.r_mm) / 2, y: presetDefaults.cone.surround_depth_mm });
    const surroundArc = current.entities.filter(e => e.kind === 'arc').at(-1)!;
    if (surroundArc.kind !== 'arc') throw new Error('Expected drawn surround');
    const surroundId = surroundArc.end;
    await key('l'); await clickWorld({ x: surround.r_mm, y: 0 }); await clickWorld({ x: rim.r_mm, y: 0 });
    await key('Escape');
    expect(current.entities.filter(e => !e.construction).map(e => e.kind)).toEqual(['arc', 'line', 'arc', 'line']);
    await key('d'); await pick(coneId); await pick('axis');
    await previewPlace({ x: 4, y: 3 }, 'distanceX'); await input(String(presetDefaults.cone.radius_mm)); await key('Enter', field()!);
    await pick(capId); await pick('baffle');
    await previewPlace({ x: 18, y: -2 }, 'distanceY'); await input(String(presetDefaults.cone.depth_mm)); await key('Enter', field()!);
    await pick(coneId); await pick(surroundId);
    const a = current.points.find(p => p.id === coneId)!, b = current.points.find(p => p.id === surroundId)!;
    await previewPlace({ x: (a.x + b.x) / 2, y: 2 }, 'distanceX'); await input(String(presetDefaults.cone.surround_width_mm)); await key('Enter', field()!);
    const values = presetDefaults.cone;
    expect(current.points.find(p => p.id === coneId)!.x).toBeCloseTo(values.radius_mm, 6);
    expect(current.points.find(p => p.id === capId)!.y).toBeCloseTo(-values.depth_mm, 6);
    expect(current.points.find(p => p.id === surroundId)!.x - current.points.find(p => p.id === coneId)!.x).toBeCloseTo(values.surround_width_mm, 6);
    const lowered = lower(current, empty.meta);
    expect(lowered.ok).toBe(true);
  });
  it('preserves the exact lowered document when dimension picks and previews are canceled', async () => {
    const document = expandedPreset('cone'); const initial = seed(document);
    const before = JSON.stringify(lower(initial.sketch, initial.meta));
    await mount(initial.sketch); await pick('cone.rim'); await pick('axis');
    await pointer('pointermove', screen({ x: 4, y: 3 }));
    await key('Escape'); await key('Escape');
    expect(JSON.stringify(lower(current, initial.meta))).toBe(before);
    expect(current).toEqual(initial.sketch);
  });
});


it('picks a cone slope and the axis, places 30 degrees, and undoes the whole dimension', async () => {
  const initial = fixture('axis');
  await mount(initial);
  await pick('line'); await pick('axis');
  const place = screen({ x: 18, y: 16 });
  await pointer('pointermove', place); await pointer('pointerdown', place); await pointer('pointerup', place);
  expect(field()).not.toBeNull();
  await input('30'); await key('Enter', field()!);
  expect(current.constraints.at(-1)).toMatchObject({ kind: 'angle', value: Math.PI / 6, driving: true });
  const p = current.points.find(p => p.id === 'p')!, q = current.points.find(p => p.id === 'q')!;
  expect(Math.atan2(q.x - p.x, q.y - p.y)).toBeCloseTo(Math.PI / 6, 8);
  expect(current.points.find(p => p.id === 'origin')).toEqual(initial.points.find(p => p.id === 'origin'));
  expect(current.points.find(p => p.id === 'up')).toEqual(initial.points.find(p => p.id === 'up'));
  await act(async () => button('Undo').click());
  expect(current).toEqual(initial);
});
