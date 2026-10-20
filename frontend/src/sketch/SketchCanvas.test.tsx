import { act, useEffect, useRef, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SketchCanvas } from './SketchCanvas';
import { SketchController } from './controller';
import { applyCommand, importedSketch } from './ops';
import type { Sketch } from './model';
import { SketchSession, type Diagnosis } from './session';
import { initialDiagnosis, realSessionReady } from './testSupport';
import { readFileSync } from 'node:fs';
import { seed } from './adapters/sourceContour';
import { sketchViewKey } from './viewPreferences';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';

let host: HTMLDivElement, root: Root;
const base = () => importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }], [{ kind: 'line', id: 'line', p1: 'p', p2: 'q' }]);
function Harness({ initial = base(), diagnosis, role }: { initial?: Sketch; diagnosis?: Diagnosis; role?: string }) {
  const [state, setState] = useState(initial), [result, setResult] = useState(diagnosis);
  const [session, setSession] = useState<SketchController<null>>();
  const handle = useRef<SketchController<null> | undefined>(undefined);
  useEffect(() => {
    const controller = new SketchController({ sketch: initial, meta: null }, [], (s, c) => { const r = applyCommand(s.sketch, c); return 'error' in r ? r : { ...s, sketch: r.sketch }; }, (s, d) => { setState(s.sketch); setResult(diagnosis ?? d); setSession(controller); }, undefined, undefined, (s, d) => { setState(s.sketch); setResult(diagnosis ?? d); });
    handle.current = controller; return () => controller.destroy();
  }, [initial, diagnosis]);
  return <SketchCanvas sketch={state} diagnosis={result} session={session} references={[]} problems={[]} entityClassName={() => role ?? ''}/>;
}
beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.removeItem(sketchViewKey);
  await realSessionReady();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const button = (label: string) => [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') ?? b.textContent) === label)!;
const svg = () => host.querySelector<SVGSVGElement>('svg[role="application"]')!;
const key = async (key: string, options = {}) => act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...options })); });
// Like a real mouse, the primary button is held from pointerdown until pointerup.
const pointer = async (element: Element, type: string, x = 0, y = 0, options = {}) => act(async () => { element.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, buttons: type === 'pointerup' ? 0 : 1, ...options })); });
async function mount(initial = base(), diagnosis?: Diagnosis, role?: string) {
  await act(async () => root.render(<Harness initial={initial} diagnosis={diagnosis} role={role}/>));
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
}
function coordinate(id: string) { const p = host.querySelector(`[data-id="${id}"]`)!; return { element: p, x: Number(p.getAttribute('cx')), y: Number(p.getAttribute('cy')) }; }

describe('SketchCanvas with real slvs', () => {
  it('opens the current arc mode with A and toggles only while an arc tool is active', async () => {
    await mount(); await key('a');
    expect(button('3-point arc (A)').getAttribute('aria-pressed')).toBe('true');
    await key('a'); expect(button('Tangent arc (A)').getAttribute('aria-pressed')).toBe('true');
    await key('Escape'); await key('a');
    expect(button('Tangent arc (A)').getAttribute('aria-pressed')).toBe('true');
    await key('l'); await key('a');
    expect(button('Tangent arc (A)').getAttribute('aria-pressed')).toBe('true');
  });
  it('keeps a rejection tooltip through small jitter, clears after eight pixels or 1.5 seconds', async () => {
    await mount(); await act(async () => button('3-point arc (A)').click());
    vi.useFakeTimers();
    try {
      for (const [x, y] of [[100, 100], [220, 100], [160, 100]]) await pointer(svg(), 'pointerdown', x, y, { ctrlKey: true });
      await pointer(svg(), 'pointermove', 163, 102, { ctrlKey: true });
      expect(host.querySelector('[role="tooltip"]')?.textContent).toContain('collinear');
      await act(async () => { await vi.advanceTimersByTimeAsync(1499); });
      expect(host.querySelector('[role="tooltip"]')).not.toBeNull();
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(host.querySelector('[role="tooltip"]')).toBeNull();
      await pointer(svg(), 'pointerdown', 160, 100, { ctrlKey: true });
      expect(host.querySelector('[role="tooltip"]')).not.toBeNull();
      await pointer(svg(), 'pointermove', 170, 100, { ctrlKey: true });
      expect(host.querySelector('[role="tooltip"]')).toBeNull();
    } finally { vi.useRealTimers(); }
  });

  it('starts with 3-point arcs and previews the chord, then the actual arc through the cursor', async () => {
    await mount();
    expect(button('3-point arc (A)')).toBeDefined();
    await act(async () => button('3-point arc (A)').click());
    await pointer(svg(), 'pointerdown', 100, 100, { ctrlKey: true });
    await pointer(svg(), 'pointermove', 220, 100, { ctrlKey: true });
    expect(host.querySelector('line.sketch-preview')).not.toBeNull();
    expect(host.textContent).toContain('end 2 of 3');
    await pointer(svg(), 'pointerdown', 220, 100, { ctrlKey: true });
    await pointer(svg(), 'pointermove', 160, 70, { ctrlKey: true });
    const preview = host.querySelector('path.sketch-preview')!;
    expect(preview.getAttribute('d')).toContain(' A');
    expect(host.textContent).toContain('point on arc 3 of 3');
    const before = preview.getAttribute('d');
    await pointer(svg(), 'pointermove', 160, 50, { ctrlKey: true });
    expect(host.querySelector('path.sketch-preview')?.getAttribute('d')).not.toBe(before);
    await pointer(svg(), 'pointerdown', 160, 50, { ctrlKey: true });
    expect(host.querySelector('[aria-label^="Select arc"]')).not.toBeNull();
  });
  it('falls back from tangent to 3-point on empty space and explains the next pick', async () => {
    await mount(); await key('a'); await key('a');
    await pointer(svg(), 'pointerdown', 100, 100, { ctrlKey: true });
    expect(button('3-point arc (A)').getAttribute('aria-pressed')).toBe('true');
    expect(host.textContent).toContain('No chain end here: started a 3-point arc');
    expect(host.querySelector('line.sketch-preview')).not.toBeNull();
    await pointer(svg(), 'pointerdown', 220, 100, { ctrlKey: true });
    await pointer(svg(), 'pointermove', 160, 70, { ctrlKey: true });
    expect(host.querySelector('path.sketch-preview')).not.toBeNull();
  });
  it('highlights open ends, rejects the pole at the cursor and previews a tangent arc', async () => {
    await mount(); await key('a'); await key('a');
    expect(host.querySelectorAll('.sketch-point.valid-start')).toHaveLength(1);
    expect(host.querySelector('.sketch-point.valid-start')?.getAttribute('data-id')).toBe('q');
    const p = coordinate('p');
    await pointer(p.element, 'pointerdown', p.x, p.y);
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain('no incoming curve');
    expect(host.querySelector<HTMLElement>('[role="tooltip"]')!.style.left).not.toBe('');
    expect(host.querySelector('.sketch-preview')).toBeNull();
    const q = coordinate('q');
    await pointer(q.element, 'pointerdown', q.x, q.y);
    await pointer(svg(), 'pointermove', q.x + 40, q.y - 40, { ctrlKey: true });
    expect(host.querySelector('[role="tooltip"]')).toBeNull();
    expect(host.querySelector('path.sketch-preview')?.getAttribute('d')).toContain(' A');
  });
  it('shows a rejected collinear arc pick next to the pointer and allows a corrected pick', async () => {
    await mount(); await act(async () => button('3-point arc (A)').click());
    for (const [x, y] of [[100, 100], [220, 100], [160, 100]]) await pointer(svg(), 'pointerdown', x, y, { ctrlKey: true });
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain('collinear');
    await pointer(svg(), 'pointerdown', 160, 70, { ctrlKey: true });
    expect(host.querySelector('[role="tooltip"]')).toBeNull();
    expect(host.querySelector('[aria-label^="Select arc"]')).not.toBeNull();
  });
  it('hides centre handles and snaps at rest, revealing them on arc hover or selection', async () => {
    const result = applyCommand(base(), { kind: 'addArc3', start: { x: 2, y: 2 }, end: { x: 8, y: 2 }, through: { x: 5, y: 4 } });
    if ('error' in result) throw new Error(result.error);
    const arc = result.sketch.entities.find(e => e.kind === 'arc')!;
    if (arc.kind !== 'arc') throw new Error('Expected arc');
    await mount(result.sketch);
    expect(host.querySelector(`[data-id="${arc.center}"]`)).toBeNull();
    await pointer(host.querySelector(`[data-id="${arc.id}"]`)!, 'pointermove');
    expect(host.querySelector(`[data-id="${arc.center}"]`)).not.toBeNull();
    const centre = coordinate(arc.center);
    await pointer(svg(), 'pointermove', 0, 0);
    expect(host.querySelector(`[data-id="${arc.center}"]`)).toBeNull();
    await act(async () => button('3-point arc (A)').click());
    await pointer(svg(), 'pointermove', centre.x, centre.y);
    expect(host.querySelector('[aria-label="Pending center snap"]')).toBeNull();
    await key('Escape');
    await pointer(host.querySelector(`[data-id="${arc.id}"]`)!, 'pointerdown');
    expect(host.querySelector('.sketch-centre')).not.toBeNull();
    await pointer(host.querySelector(`[data-id="${arc.center}"]`)!, 'pointerdown', centre.x, centre.y);
    await pointer(svg(), 'pointerup');
    await pointer(svg(), 'pointermove', 0, 0);
    expect(host.querySelector(`[data-id="${arc.center}"]`)).not.toBeNull();
  });
  it('uses solid status-coloured profiles, role weights, and orange dashed construction', async () => {
    const style = document.createElement('style'); style.textContent = new TextDecoder().decode(readFileSync('src/sketch/sketchCanvas.css')); document.head.append(style);
    const sketch = base(), diagnosis = initialDiagnosis(sketch);
    try {
      for (const role of ['moving', 'rigid']) {
        for (const status of ['under', 'determined', 'conflicting']) {
          const s = { ...sketch, constraints: status === 'conflicting' ? [{ kind: 'horizontal' as const, id: 'h', line: 'line' }] : [] };
          const d: Diagnosis = { ...diagnosis, dof: status === 'determined' ? 0 : 4, constraintStates: status === 'conflicting' ? { h: 'conflicting' as const } : {} };
          await act(async () => root.render(<SketchCanvas sketch={s} diagnosis={d} references={[]} problems={[]} entityClassName={() => role}/>));
          // Conflict references are highlighted until the banner is dismissed; check the resting status.
          if (status === 'conflicting') await act(async () => button('Dismiss').click());
          const entity = host.querySelector('.sketch-entity')!, computed = getComputedStyle(entity);
          expect(entity.classList.contains(status)).toBe(true);
          expect(computed.strokeDasharray).toBe('none');
          expect(computed.stroke).toBe(`var(--sketch-${status})`);
          expect(computed.strokeWidth).toBe(role === 'moving' ? '2' : '3.5');
          expect(host.querySelector(`.sketch-point.${status}`)).not.toBeNull();
        }
      }
      sketch.entities[0].construction = true;
      await act(async () => root.render(<SketchCanvas sketch={sketch} diagnosis={diagnosis} references={[]} problems={[]}/>));
      const construction = getComputedStyle(host.querySelector('.sketch-entity.construction')!);
      expect(construction.strokeDasharray).toBe('6 4'); expect(construction.stroke).toBe('var(--sketch-construction)'); expect(construction.strokeWidth).toBe('1.5');
      expect(style.textContent).not.toContain('--source-moving');
    } finally { style.remove(); }
  });
  it('converts the selected curve in place from the contextual toolbar and undoes in one step', async () => {
    await mount(); await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    await act(async () => button('Make arc').click());
    expect(host.querySelector('[aria-label="Select arc line"]')).not.toBeNull();
    expect(button('Make line')).toBeDefined();
    await act(async () => button('Make line').click());
    expect(host.querySelector('[aria-label="Select line line"]')).not.toBeNull();
    expect(host.querySelector('.sketch-centre')).toBeNull();
    await key('z', { ctrlKey: true });
    expect(host.querySelector('[aria-label="Select arc line"]')).not.toBeNull();
    await key('z', { ctrlKey: true });
    expect(host.querySelector('[aria-label="Select line line"]')).not.toBeNull();
  });
  it('renders geometry, glyphs, dimension units and status colours including the large-sketch fallback', async () => {
    let s = base(); const r = applyCommand(s, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'p', b: 'q', value: 10, driving: true } });
    if ('error' in r) throw new Error(r.error); s = r.sketch;
    const diagnosis = { ...initialDiagnosis(s), dof: 0, pointDetermined: undefined };
    await mount(s, diagnosis);
    expect(host.querySelector('.sketch-entity.determined')).not.toBeNull();
    expect(host.querySelector('.sketch-glyph')?.textContent).toContain('10.00 mm');
    expect(host.textContent).toContain('Fully constrained');
    await act(async () => root.render(<SketchCanvas sketch={s} diagnosis={{ ...diagnosis, dof: 2 }} references={[]} problems={[{ code: 'fold', ids: ['line'], message: 'Invalid fold' }]}/>));
    expect(host.querySelector('.sketch-entity.under')).not.toBeNull(); expect(host.querySelector('.sketch-problem')).not.toBeNull();
    await act(async () => root.render(<SketchCanvas sketch={s} diagnosis={{ ...diagnosis, constraintStates: { [s.constraints[0].id]: 'conflicting' } }} references={[]} problems={[]}/>));
    expect(host.querySelector('.sketch-entity.conflicting')).not.toBeNull();
  });
  it('shows only selection-valid actions and keeps dimension hints contextual', async () => {
    await mount();
    expect(host.querySelector('[aria-label="Selection actions"]')).toBeNull();
    expect(host.querySelector('[aria-label="Dimension measurement"]')).toBeNull();
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    const labels = () => [...host.querySelectorAll('[aria-label="Selection actions"] button')].map(b => b.textContent);
    expect(labels()).toEqual(['Construction (X)', 'Make arc', 'Delete selection']);
    expect(button('Horizontal').disabled).toBe(false); expect(button('Coincident').disabled).toBe(true);
    expect(host.querySelector('.sketch-surface > [aria-label="Selection actions"]')).not.toBeNull();
    await pointer(svg(), 'pointerdown');
    await pointer(coordinate('p').element, 'pointerdown'); await pointer(svg(), 'pointerup');
    expect(labels()).toEqual(['Delete selection']);
    expect(button('Fix').disabled).toBe(false); expect(button('Horizontal').disabled).toBe(true);
    await pointer(coordinate('q').element, 'pointerdown', 0, 0, { shiftKey: true }); await pointer(svg(), 'pointerup');
    expect(labels()).toEqual(['Delete selection']);
    expect(button('Horizontal').disabled).toBe(false); expect(button('Coincident').disabled).toBe(false);
    await act(async () => button('Dimension (D)').click());
    expect(host.querySelector('[aria-label="Dimension measurement"]')).toBeNull();
    expect(host.textContent).toContain('move to choose; click to place; type the value');
    await key('Escape');
    expect(host.querySelector('[aria-label="Dimension measurement"]')).toBeNull();
  });
  it('keeps the canvas offset and flow siblings unchanged when dimensioning starts', async () => {
    const style = document.createElement('style');
    style.textContent = new TextDecoder().decode(readFileSync('src/sketch/sketchCanvas.css'));
    document.head.append(style);
    try {
      await mount();
      const surface = host.querySelector<HTMLElement>('.sketch-surface')!;
      const siblings = [...surface.parentElement!.children];
      const offset = svg().getBoundingClientRect().top;
      await act(async () => button('Dimension (D)').click());
      expect(host.querySelector('.sketch-dimension-context')).toBeNull();
      expect([...surface.parentElement!.children]).toEqual(siblings);
      expect(svg().getBoundingClientRect().top).toBe(offset);
      expect(button('Apply dimension')).toBeUndefined();
      await key('Escape'); expect(svg().getBoundingClientRect().top).toBe(offset);
    } finally { style.remove(); }
  });
  it('previews then places a measured dimension, selects its focused value and keeps it on Esc', async () => {
    await mount(); await act(async () => button('Dimension (D)').click());
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
    await pointer(svg(), 'pointermove', 320, 60);
    expect(host.querySelector('.sketch-dimension-preview')).not.toBeNull();
    await pointer(svg(), 'pointerdown', 320, 60); await pointer(svg(), 'pointerup', 320, 60);
    const input = host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')!;
    expect(input.value).toBe('10'); expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0); expect(input.selectionEnd).toBe(2);
    await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
    expect(host.querySelector('.sketch-glyph')?.textContent).toContain('10.00 mm');
    // Redundancy requires an explicit reference choice.
    await act(async () => button('Dimension (D)').click());
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    await pointer(svg(), 'pointerdown', 320, 80); await pointer(svg(), 'pointerup', 320, 80);
    expect(host.querySelector('.sketch-glyph.reference')).toBeNull();
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => button('Make it a reference (driven) dimension').click());
    expect(host.querySelector('.sketch-glyph.reference')?.textContent).toContain('(10.00 mm)');
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
  });
  it('dimensions sequential points and lines and opens the radius editor on an arc', async () => {
    const initial = importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }, { id: 'r', x: 10, y: 10 }], [
      { kind: 'line', id: 'line', p1: 'p', p2: 'q' }, { kind: 'line', id: 'other', p1: 'q', p2: 'r' },
      { kind: 'arc', id: 'arc', center: 'q', start: 'r', end: 'p', ccw: true },
    ]);
    await mount(initial); await key('d');
    await pointer(coordinate('p').element, 'pointerdown');
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
    await pointer(coordinate('r').element, 'pointerdown');
    expect(host.querySelector('[aria-label^="Dimension distance"]')).toBeNull();
    await pointer(svg(), 'pointermove', 600, 100);
    await pointer(svg(), 'pointerdown', 600, 100); await pointer(svg(), 'pointerup', 600, 100);
    expect(host.querySelector('[aria-label^="Dimension distance"]')).not.toBeNull();
    expect(document.activeElement).toBe(host.querySelector('[aria-label="Dimension value"]'));
    await key('Escape'); await key('d');
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown', 0, 0);
    await pointer(host.querySelector('[data-id="other"]')!, 'pointerdown', 0, 0);
    await pointer(svg(), 'pointermove', 500, 80);
    await pointer(svg(), 'pointerdown', 500, 80); await pointer(svg(), 'pointerup', 500, 80);
    expect(host.querySelector('[aria-label^="Dimension angle"]')).not.toBeNull();
    expect(host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')!.value).toBe('90');
    await key('Escape'); await key('d');
    await pointer(host.querySelector('[data-id="arc"]')!, 'pointerdown');
    await pointer(svg(), 'pointermove', 500, 200);
    await pointer(svg(), 'pointerdown', 500, 200); await pointer(svg(), 'pointerup', 500, 200);
    expect(host.querySelector('[aria-label^="Dimension radius"]')).not.toBeNull();
    expect(host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')!.value).toBe('10');
  });
  it('requires the same label within 400 ms and preserves keyboard and synthetic editing', async () => {
    const r = applyCommand(base(), { kind: 'addConstraint', constraint: { kind: 'distance', a: 'p', b: 'q', value: 10, driving: true } });
    if ('error' in r) throw new Error(r.error);
    await mount(r.sketch);
    const time = vi.spyOn(performance, 'now').mockReturnValue(0), hit = host.querySelector('.sketch-label-hit')!;
    await pointer(hit, 'pointerdown'); time.mockReturnValue(401); await pointer(hit, 'pointerdown');
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
    await pointer(host.querySelector('.sketch-glyph')!, 'dblclick');
    expect(host.querySelector('[aria-label="Dimension value"]')).not.toBeNull();
  });
  it('fits the first measured viewport and fits replacement geometry on request', async () => {
    let resize!: ResizeObserverCallback;
    vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { resize = callback; } observe() {} disconnect() {} });
    const initial = base();
    await act(async () => root.render(<SketchCanvas sketch={initial} references={[]} problems={[]} fitRequest={0}/>));
    await act(async () => resize([{ contentRect: { width: 280, height: 360 } } as ResizeObserverEntry], {} as ResizeObserver));
    const inside = () => [...host.querySelectorAll('.sketch-point')].every(p => Number(p.getAttribute('cx')) >= 32 && Number(p.getAttribute('cx')) <= 248 && Number(p.getAttribute('cy')) >= 32 && Number(p.getAttribute('cy')) <= 328);
    expect(inside()).toBe(true);
    const replacement = { ...initial, points: initial.points.map(p => ({ ...p, x: p.x * 100, y: p.y + 200 })) };
    await act(async () => root.render(<SketchCanvas sketch={replacement} references={[]} problems={[]} fitRequest={1}/>));
    expect(inside()).toBe(true);
  });
  it('renders structural references in one context layer without editable point or curve hit targets', async () => {
    const { sketch, meta } = seed(INITIAL_SOURCE);
    await act(async () => root.render(<SketchCanvas sketch={sketch} references={meta.references} problems={[]}/>));
    const refs = sketch.entities.filter(e => meta.references.some(r => r.id === e.id));
    const refPoints = refs.flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : []);
    for (const id of [...refPoints, ...refs.map(e => e.id)]) expect(host.querySelector(`[data-id="${id}"]`)).toBeNull();
    expect(host.querySelectorAll('.sketch-context-layer')).toHaveLength(1);
    expect(host.querySelectorAll('.sketch-context-item')).toHaveLength(2);
    expect(host.querySelector('[data-context-id="axis"] > path:not(.sketch-context-hit)')?.getAttribute('d')).toContain(',360');
    expect([...host.querySelectorAll('.sketch-point-hit')].every(p => Number(p.getAttribute('r')) >= 10)).toBe(true);
    expect([...host.querySelectorAll('.sketch-point')].every(p => Number(p.getAttribute('r')) === 4)).toBe(true);
    expect(host.querySelector('.sketch-curve-hit')).not.toBeNull();
  });
  it('prevents pointer and wheel defaults and focuses without scrolling', async () => {
    await mount();
    const focus = vi.spyOn(svg(), 'focus');
    const event = new MouseEvent('pointerdown', { bubbles: true, cancelable: true });
    await act(async () => coordinate('q').element.dispatchEvent(event));
    expect(event.defaultPrevented).toBe(true);
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    const wheel = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 100 });
    await act(async () => svg().dispatchEvent(wheel));
    expect(wheel.defaultPrevented).toBe(true);
    await pointer(svg(), 'pointerup');
  });
  it('edits a driving dimension on pointer double-click and removes its glyph with Delete', async () => {
    const r = applyCommand(base(), { kind: 'addConstraint', constraint: { kind: 'distance', a: 'p', b: 'q', value: 10, driving: true } });
    if ('error' in r) throw new Error(r.error);
    await mount(r.sketch);
    const hit = host.querySelector('.sketch-label-hit')!;
    expect(Number(hit.getAttribute('height'))).toBeGreaterThanOrEqual(28);
    const capture = vi.fn(); svg().setPointerCapture = capture;
    await pointer(hit, 'pointerdown'); await pointer(svg(), 'pointerup');
    expect(host.querySelector('.sketch-glyph.selected')).not.toBeNull();
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
    await pointer(hit, 'pointerdown'); await pointer(svg(), 'pointerup');
    expect(capture).not.toHaveBeenCalled();
    const field = host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')!;
    expect(document.activeElement).toBe(field);
    expect(field.selectionStart).toBe(0); expect(field.selectionEnd).toBe(field.value.length);
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, '12'); field.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => button('Apply dimension value').click());
    expect(host.querySelector('.sketch-glyph')?.textContent).toContain('12.00 mm');
    await pointer(host.querySelector('.sketch-glyph')!, 'pointerdown'); await key('Delete');
    expect(host.querySelector('.sketch-glyph')).toBeNull();
  });
  it('drags via the session, cancels with Esc and undoes one whole gesture', async () => {
    await mount(); const q = coordinate('q');
    await pointer(q.element, 'pointerdown', q.x, q.y);
    await pointer(svg(), 'pointermove', q.x + 30, q.y - 30);
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    expect(coordinate('q').y).toBeLessThan(q.y);
    await key('Escape'); expect(coordinate('q').y).toBeCloseTo(q.y);
    await pointer(coordinate('q').element, 'pointerdown', q.x, q.y);
    await pointer(svg(), 'pointermove', q.x + 30, q.y - 30); await pointer(svg(), 'pointerup');
    expect(coordinate('q').y).toBeLessThan(q.y);
    await key('z', { ctrlKey: true }); expect(coordinate('q').y).toBeCloseTo(q.y);
    await key('z', { ctrlKey: true, shiftKey: true }); expect(coordinate('q').y).toBeLessThan(q.y);
  });
  it('places typed length and angle as dimensions in a single undo gesture', async () => {
    await mount(); await key('l'); const q = coordinate('q');
    await pointer(q.element, 'pointerdown', q.x, q.y); await pointer(svg(), 'pointerup');
    await key('5');
    const angle = host.querySelector<HTMLInputElement>('[aria-label="Line angle"]')!;
    await act(async () => { angle.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(angle, '90'); angle.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => angle.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(host.textContent).toContain('5.00 mm'); expect(host.textContent).toContain('90.00°');
    expect(host.querySelectorAll('.sketch-entity:not(.construction)')).toHaveLength(2);
    await key('z', { metaKey: true }); expect(host.querySelectorAll('.sketch-entity')).toHaveLength(1);
  });
  it('records drag frame timing only when the performance flag is enabled', async () => {
    const old = window.location.href;
    window.history.replaceState(null, '', '?sketchPerf=1');
    try {
      await mount(); const q = coordinate('q');
      await pointer(q.element, 'pointerdown', q.x, q.y); await pointer(svg(), 'pointermove', q.x + 20, q.y - 20);
      await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
      await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
      expect(window.__sketchPerf!.frames.length).toBeGreaterThan(0);
      expect(window.__sketchPerf!.frames.every(t => t >= 0)).toBe(true);
      expect(window.__sketchPerf!.solveMs).toHaveLength(window.__sketchPerf!.frames.length);
      expect(window.__sketchPerf!.solveMs.every(t => t > 0)).toBe(true);
      window.__sketchPerf!.reset(); expect(window.__sketchPerf!.frames).toEqual([]);
      await pointer(svg(), 'pointerup');
    } finally { window.history.replaceState(null, '', old); delete window.__sketchPerf; }
  });
  it('routes shortcuts, typed boxes, snapping suppression and chain cancellation', async () => {
    await mount(); expect(window.__sketchPerf).toBeUndefined(); await key('l'); expect(button('Line (L)').getAttribute('aria-pressed')).toBe('true');
    await pointer(svg(), 'pointerdown', 150, 100, { ctrlKey: true });
    await key('5'); expect(host.querySelector('[aria-label="Line length"]')).not.toBeNull();
    const field = host.querySelector('[aria-label="Line length"]')!;
    await act(async () => field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(host.querySelector('[aria-label="Line length"]')).toBeNull();
    await key('Escape'); expect(button('Select (Esc)').getAttribute('aria-pressed')).toBe('true');
    await key('a'); expect(button('3-point arc (A)').getAttribute('aria-pressed')).toBe('true');
    await key('a'); expect(button('Tangent arc (A)').getAttribute('aria-pressed')).toBe('true');
    await key('a'); expect(button('3-point arc (A)').getAttribute('aria-pressed')).toBe('true');
    await key('s'); expect(button('Split (S)').getAttribute('aria-pressed')).toBe('true');
    await key('d'); expect(button('Dimension (D)').getAttribute('aria-pressed')).toBe('true');
  });
  it('fix 2: refocuses and selects the inline dimension value after every refusal', async () => {
    const r = applyCommand(base(), { kind: 'addConstraint', constraint: { kind: 'distance', a: 'p', b: 'q', value: 10, driving: false } });
    if ('error' in r) throw new Error(r.error);
    await mount(r.sketch);
    await pointer(host.querySelector('.sketch-glyph')!, 'dblclick');
    const field = host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')!;
    for (let i = 0; i < 2; i++) {
      await act(async () => { button('Apply dimension value').focus(); button('Apply dimension value').click(); });
      expect(host.querySelector('[role="alert"]')?.textContent).toContain('driving dimension');
      expect(document.activeElement).toBe(field);
      expect(field.selectionStart).toBe(0); expect(field.selectionEnd).toBe(field.value.length);
    }
  });
  it('fix 8: preserves Space on toolbar and context buttons and pans only from the surface', async () => {
    await mount();
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    for (const control of [button('Line (L)'), button('Horizontal')]) {
      const event = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
      await act(async () => { control.focus(); control.dispatchEvent(event); });
      expect(event.defaultPrevented).toBe(false);
      await key(' ', { });
      await act(async () => svg().dispatchEvent(new KeyboardEvent('keyup', { key: ' ', bubbles: true })));
    }
    const space = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
    await act(async () => { svg().focus(); svg().dispatchEvent(space); });
    expect(space.defaultPrevented).toBe(true);
    const before = coordinate('q').x;
    await pointer(svg(), 'pointerdown', 100, 100); await pointer(svg(), 'pointermove', 120, 100); await pointer(svg(), 'pointerup');
    expect(coordinate('q').x).toBeCloseTo(before + 20);
    await act(async () => {
      svg().blur(); svg().dispatchEvent(new MouseEvent('pointerover', { bubbles: true }));
      window.dispatchEvent(new KeyboardEvent('keyup', { key: ' ' }));
    });
    const hoverSpace = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
    await act(async () => document.body.dispatchEvent(hoverSpace));
    expect(hoverSpace.defaultPrevented).toBe(true);
    await pointer(svg(), 'pointerdown', 100, 100); await pointer(svg(), 'pointermove', 120, 100); await pointer(svg(), 'pointerup');
    expect(coordinate('q').x).toBeCloseTo(before + 40);
    await act(async () => window.dispatchEvent(new KeyboardEvent('keyup', { key: ' ' })));
  });
  it('fix 9: gives the drawing one tab stop with arrow navigation, activation and deletion', async () => {
    await mount();
    expect(svg().querySelectorAll('[tabindex="0"]')).toHaveLength(0);
    expect(svg().getAttribute('tabindex')).toBe('0');
    await act(async () => svg().focus()); await key('ArrowRight');
    const active = document.activeElement!;
    expect(active.getAttribute('aria-label')).toBe('Select line line');
    await act(async () => active.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })));
    expect(host.querySelector('.sketch-entity.selected')).not.toBeNull();
    await act(async () => active.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true })));
    expect(host.querySelector('.sketch-entity')).toBeNull();
  });
  it('fix 10: queues fast line clicks and run actions in order', async () => {
    await mount(); await key('l');
    await act(async () => {
      for (const [x, y] of [[150, 100], [250, 130], [350, 160], [450, 190]])
        svg().dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: x, clientY: y, ctrlKey: true }));
    });
    expect(host.querySelectorAll('.sketch-entity')).toHaveLength(4);
    await key('Escape');
    await act(async () => { for (let i = 0; i < 2; i++) svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true })); });
    expect(host.querySelectorAll('.sketch-entity')).toHaveLength(2);
  });
  it('two ordinary line picks preview and place only an angle without an extra length', async () => {
    const initial = importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }, { id: 'r', x: 10, y: 10 }], [
      { kind: 'line', id: 'line', p1: 'p', p2: 'q' }, { kind: 'line', id: 'other', p1: 'q', p2: 'r' },
    ]);
    await mount(initial); await key('d');
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown', 0, 0);
    expect(host.querySelector('.sketch-glyph')).toBeNull();
    await pointer(host.querySelector('[data-id="other"]')!, 'pointerdown', 0, 0);
    expect(host.querySelectorAll('.sketch-glyph')).toHaveLength(0);
    await pointer(svg(), 'pointermove', 500, 80);
    await pointer(svg(), 'pointerdown', 500, 80); await pointer(svg(), 'pointerup', 500, 80);
    expect(host.querySelector('[aria-label^="Dimension angle"]')).not.toBeNull();
    expect(host.querySelectorAll('.sketch-glyph')).toHaveLength(1);
  });

  it('shows values at rest, names on hover or selection, and sizes the hit rect to the displayed text', async () => {
    const sketch = base();
    sketch.constraints.push({ kind: 'distance', id: 'length', a: 'p', b: 'q', value: 10, driving: true, label: 'Long named length' });
    await mount(sketch);
    const label = () => host.querySelector('[data-constraint-id="length"] text')!;
    const width = () => Number(host.querySelector('[data-constraint-id="length"] rect')!.getAttribute('width'));
    expect(label().lastChild!.textContent).toBe('10.00 mm');
    expect(label().getAttribute('aria-label')).toContain('Long named length');
    expect(label().querySelector('title')!.textContent).toContain('Long named length 10.00 mm');
    const shortWidth = width();
    await pointer(label(), 'pointermove');
    expect(label().lastChild!.textContent).toBe('Long named length 10.00 mm');
    expect(label().classList.contains('selected')).toBe(true); expect(width()).toBeGreaterThan(shortWidth);
    await pointer(svg(), 'pointermove'); expect(width()).toBe(shortWidth);
    await pointer(label(), 'pointerdown'); await pointer(svg(), 'pointermove');
    expect(label().lastChild!.textContent).toBe('Long named length 10.00 mm');
    await pointer(svg(), 'pointerdown'); expect(label().lastChild!.textContent).toBe('10.00 mm');
    await act(async () => button('Names').click());
    expect(label().lastChild!.textContent).toBe('Long named length 10.00 mm');
    expect(host.querySelector('.sketch-curve-name')!.textContent).toBe('Line 1');
  });
  it('remembers view toggles across reopen and reveals selected, conflicting and problem items in hidden categories', async () => {
    const sketch = base();
    sketch.constraints.push({ kind: 'distance', id: 'length', a: 'p', b: 'q', value: 10, driving: true, label: 'Length' },
      { kind: 'horizontal', id: 'horizontal', line: 'line' });
    const render = async (selection: string[] = [], conflict = false, problem = false) => act(async () => root.render(<SketchCanvas sketch={sketch} references={[]}
      diagnosis={{ ...initialDiagnosis(sketch), constraintStates: conflict ? { length: 'conflicting', horizontal: 'conflicting' } : {} }}
      problems={problem ? [{ code: 'issue', ids: ['length', 'horizontal'], message: 'Check these items' }] : []} selection={selection}/>));
    await render();
    expect(button('Dimensions').getAttribute('aria-pressed')).toBe('true');
    expect(button('Names').getAttribute('aria-pressed')).toBe('false');
    expect(button('Constraints').getAttribute('aria-pressed')).toBe('true');
    await act(async () => { button('Dimensions').click(); button('Constraints').click(); });
    expect(host.querySelector('[data-constraint-id="length"]')).toBeNull();
    expect(host.querySelector('[data-constraint-id="horizontal"]')).toBeNull();
    await render(['length']); expect(host.querySelector('[data-constraint-id="length"]')).not.toBeNull();
    expect(host.querySelector('[data-constraint-id="length"] text')!.lastChild!.textContent).toBe('Length 10.00 mm');
    await render([], true);
    expect(host.querySelectorAll('[data-constraint-id]')).toHaveLength(2);
    await render([], false, true); expect(host.querySelectorAll('[data-constraint-id]')).toHaveLength(2);
    await act(async () => button('Names').click());
    await act(async () => root.render(null)); await render();
    expect(button('Dimensions').getAttribute('aria-pressed')).toBe('false');
    expect(button('Constraints').getAttribute('aria-pressed')).toBe('false');
    expect(button('Names').getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelectorAll('[data-constraint-id]')).toHaveLength(0);
    expect(host.querySelector('.sketch-curve-name')).not.toBeNull();
  });

  it('does not drag a dimension when the button was released off the canvas before the threshold', async () => {
    const initial = importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 0, y: 10 }], [{ kind: 'line', id: 'line', p1: 'p', p2: 'q' }]);
    initial.constraints.push({ kind: 'distanceY', id: 'depth', a: 'p', b: 'q', value: 10, driving: true });
    await mount(initial);
    const command = vi.spyOn(SketchController.prototype, 'command');
    const label = () => host.querySelector<SVGTextElement>('[data-constraint-id="depth"] text')!;
    const at = () => ({ x: Number(label().getAttribute('x')), y: Number(label().getAttribute('y')) });
    const start = at();
    await pointer(label(), 'pointerdown', start.x, start.y);
    // The pointerup happened outside the canvas; a later hover arrives with no button held.
    await pointer(svg(), 'pointermove', start.x - 40, start.y + 10, { buttons: 0 });
    expect(at()).toEqual(start); expect(command).not.toHaveBeenCalled();
    await pointer(svg(), 'pointermove', start.x - 60, start.y + 30, { buttons: 0 });
    expect(at()).toEqual(start); expect(command).not.toHaveBeenCalled();
  });
  it('previews a dimension text drag after four pixels and commits it once, with reset, cancel and undo', async () => {
    const initial = importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 0, y: 10 }], [{ kind: 'line', id: 'line', p1: 'p', p2: 'q' }]);
    initial.constraints.push({ kind: 'distanceY', id: 'depth', a: 'p', b: 'q', value: 10, driving: true });
    await mount(initial);
    const command = vi.spyOn(SketchController.prototype, 'command');
    const label = () => host.querySelector<SVGTextElement>('[data-constraint-id="depth"] text')!;
    const at = () => ({ x: Number(label().getAttribute('x')), y: Number(label().getAttribute('y')) });
    const start = at(), path = host.querySelector('.sketch-extension-lines')!.getAttribute('d');
    const scale = Math.abs(coordinate('p').y - coordinate('q').y) / 10;
    await pointer(label(), 'pointerdown', start.x, start.y);
    await pointer(svg(), 'pointermove', start.x - 3, start.y);
    expect(at()).toEqual(start); expect(command).not.toHaveBeenCalled();
    await pointer(svg(), 'pointermove', start.x - 4, start.y);
    expect(at().x).toBeCloseTo(start.x - 4);
    await pointer(svg(), 'pointermove', start.x - 30, start.y + 20);
    expect(at().x).toBeCloseTo(start.x - 30); expect(at().y).toBeCloseTo(start.y + 20);
    expect(host.querySelector('.sketch-extension-lines')!.getAttribute('d')).not.toBe(path);
    expect(command).not.toHaveBeenCalled();
    await pointer(svg(), 'pointerup');
    expect(command).toHaveBeenCalledTimes(1);
    expect(command.mock.calls[0][0]).toMatchObject({ kind: 'setDimensionPlacement', id: 'depth' });
    const after: Sketch = await command.mock.results[0].value;
    expect(after.constraints.find(c => c.id === 'depth')!.placement).toEqual({ offset: -24 / scale - 30 / scale, along: .5 - 20 / scale / 10 });
    await key('z', { ctrlKey: true }); expect(at()).toEqual(start);
    await key('z', { ctrlKey: true, shiftKey: true }); expect(at().x).toBeCloseTo(start.x - 30);
    await act(async () => button('Reset position').click());
    expect(at()).toEqual(start);
    expect((await command.mock.results.at(-1)!.value).constraints.find((c: { id: string }) => c.id === 'depth')).not.toHaveProperty('placement');
    command.mockClear();
    await pointer(label(), 'pointerdown', start.x, start.y);
    await pointer(svg(), 'pointermove', start.x - 30, start.y);
    await key('Escape'); expect(at()).toEqual(start); expect(command).not.toHaveBeenCalled();
    await pointer(label(), 'pointerdown', start.x, start.y);
    await pointer(svg(), 'pointermove', start.x - 30, start.y);
    await pointer(svg(), 'pointercancel'); expect(at()).toEqual(start); expect(command).not.toHaveBeenCalled();
    // Crossing the measured geometry keeps the text under the pointer without a side-switch jump.
    await pointer(label(), 'pointerdown', start.x, start.y);
    await pointer(svg(), 'pointermove', start.x + 60, start.y);
    expect(at().x).toBeCloseTo(start.x + 60);
    await pointer(svg(), 'pointercancel'); expect(at()).toEqual(start); expect(command).not.toHaveBeenCalled();
  });
  it('selects on a click with jitter and cannot drag a dimension revealed while Dimensions is off', async () => {
    const initial = base();
    initial.constraints.push({ kind: 'distanceX', id: 'length', a: 'p', b: 'q', value: 10, driving: true });
    await mount(initial);
    const command = vi.spyOn(SketchController.prototype, 'command');
    const label = () => host.querySelector('[data-constraint-id="length"] text')!;
    await pointer(label(), 'pointerdown', 100, 100);
    await pointer(svg(), 'pointermove', 102, 101); await pointer(svg(), 'pointerup');
    expect(label().classList.contains('selected')).toBe(true); expect(command).not.toHaveBeenCalled();
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
    await act(async () => button('Dimensions').click());
    expect(label()).not.toBeNull();
    const time = vi.spyOn(performance, 'now').mockReturnValue(1000);
    await pointer(label(), 'pointerdown', 100, 100);
    await pointer(svg(), 'pointermove', 150, 150); await pointer(svg(), 'pointerup');
    expect(command).not.toHaveBeenCalled();
    time.mockReturnValue(1100);
    await pointer(label(), 'pointerdown', 100, 100); await pointer(svg(), 'pointerup');
    expect(host.querySelector('[aria-label="Dimension value"]')).not.toBeNull();
  });

});


describe('constraint context controls', () => {
  it('toggles a selected dimension both ways and undoes once', async () => {
    const initial = base();
    initial.constraints.push({ id: 'length', kind: 'distance', a: 'p', b: 'q', value: 10, driving: true });
    await mount(initial);
    await pointer(host.querySelector('[data-constraint-id="length"] text')!, 'pointerdown');
    await act(async () => button('Make reference').click());
    expect(host.querySelector('[data-constraint-id="length"] .sketch-glyph.reference')).not.toBeNull();
    await act(async () => button('Make driving').click());
    expect(host.querySelector('[data-constraint-id="length"] .sketch-glyph.reference')).toBeNull();
    await act(async () => button('Undo').click());
    expect(host.querySelector('[data-constraint-id="length"] .sketch-glyph.reference')).not.toBeNull();
  });
  it('keeps named conflicts past 1.8 seconds, shows selection, dismisses, and clears on success', async () => {
    const initial = base();
    initial.entities[0].data = { name: 'Cone slope' };
    initial.constraints.push(
      { id: 'constraint-opaque-a', kind: 'horizontal', line: 'line', label: 'Level slope' },
      { id: 'constraint-opaque-b', kind: 'distance', a: 'p', b: 'q', value: 10, driving: true, label: 'Slope length' },
    );
    await mount(initial);
    const line = () => host.querySelector('.sketch-entity[data-id="line"]')!;
    await pointer(line(), 'pointerdown');
    await act(async () => button('Vertical').click());
    const banner = () => host.querySelector('.sketch-conflict-banner');
    expect(banner()?.textContent).toContain('Cone slope');
    expect(banner()?.textContent).toMatch(/Level slope|Slope length/);
    expect(banner()?.textContent).not.toContain('constraint-opaque');
    vi.useFakeTimers();
    try { await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); }
    finally { vi.useRealTimers(); }
    expect(banner()).not.toBeNull();
    await act(async () => button('Show').click());
    expect(host.querySelector('[data-constraint-id] .sketch-glyph.selected')).not.toBeNull();
    expect(line().classList.contains('selected')).toBe(true);
    await act(async () => button('Dismiss').click()); expect(banner()).toBeNull();
    await key('Escape'); await pointer(line(), 'pointerdown');
    await act(async () => button('Vertical').click()); expect(banner()).not.toBeNull();
    await act(async () => button('Construction (X)').click()); expect(banner()).toBeNull();
  });
  it('refuses a selected reference-to-driving toggle with a named persistent reason', async () => {
    const initial = base();
    initial.constraints.push(
      { id: 'held', kind: 'distance', a: 'p', b: 'q', value: 10, driving: true, label: 'Held length' },
      { id: 'measured', kind: 'distance', a: 'p', b: 'q', value: 10, driving: false, label: 'Measured length' },
    );
    await mount(initial);
    await pointer(host.querySelector('[data-constraint-id="measured"] text')!, 'pointerdown');
    await act(async () => button('Make driving').click());
    expect(host.querySelector('[data-constraint-id="measured"] .reference')).not.toBeNull();
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).toContain('Measured length');
    expect(button('Make driving')).toBeDefined();
    expect(host.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(host.querySelector('.sketch-status')?.textContent).not.toContain('Conflicts with');
    await act(async () => button('Dismiss').click());
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
  });
  it('shows a convergence failure as a solve error without a conflict banner', async () => {
    const initial = base();
    await mount(initial);
    vi.spyOn(SketchSession.prototype, 'solveFull').mockResolvedValueOnce({
      ...initialDiagnosis(initial), status: 'failed', conflicting: [], redundant: [], hasRedundancy: false,
    });
    await key('s');
    const line = host.querySelector('.sketch-entity[data-id="line"]')!;
    const [x1, y1, x2, y2] = line.getAttribute('d')!.match(/[\d.e+-]+/g)!.map(Number);
    await pointer(line, 'pointerdown', (x1 + x2) / 2, (y1 + y2) / 2);
    expect(host.querySelector('.sketch-status')?.textContent).toContain('Could not solve this change');
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
    expect(host.querySelectorAll('.sketch-entity')).toHaveLength(1);
  });
  it('clears a diagnosed conflict when the host resolves it', async () => {
    const initial = base();
    initial.constraints.push({ id: 'opaque', kind: 'horizontal', line: 'line' });
    const diagnosis = { ...initialDiagnosis(initial), status: 'failed' as const, constraintStates: { opaque: 'conflicting' as const } };
    await act(async () => root.render(<SketchCanvas sketch={initial} diagnosis={diagnosis} references={[]} problems={[]}/>));
    expect(host.querySelector('.sketch-conflict-banner')?.textContent).toContain('Horizontal (Line 1)');
    await act(async () => root.render(<SketchCanvas sketch={initial} diagnosis={initialDiagnosis(initial)} references={[]} problems={[]}/>));
    expect(host.querySelector('.sketch-conflict-banner')).toBeNull();
  });
  it('uses available constraint shortcut hints without taking the drawing keys', async () => {
    const initial = base();
    initial.points[1].y = 5;
    await mount(initial); await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    expect(button('Vertical').title).toBe('Vertical (V)');
    await key('v');
    expect(host.querySelector('[aria-label^="Constraint vertical"]')).not.toBeNull();
    await key('l'); expect(button('Line (L)').getAttribute('aria-pressed')).toBe('true');
  });
});


describe('navigation and line legend', () => {
  it('keeps Undo and Redo outside View and before Fit, with working history actions', async () => {
    await mount();
    const toolbar = host.querySelector('[aria-label="Sketch tools"]')!, view = toolbar.querySelector('[aria-label="View"]')!;
    for (const label of ['Undo', 'Redo']) {
      expect(button(label).parentElement).toBe(toolbar);
      expect(view.contains(button(label))).toBe(false);
      expect(button(label).compareDocumentPosition(button('Fit (F)')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(button('Undo').nextElementSibling).toBe(button('Redo'));
    const undo = vi.spyOn(SketchController.prototype, 'undo');
    await act(async () => button('Undo').click()); await act(async () => button('Redo').click());
    expect(undo).toHaveBeenCalledTimes(2); expect(undo).toHaveBeenNthCalledWith(1, false); expect(undo).toHaveBeenNthCalledWith(2, true);
  });
  it.each(['moving', 'rigid'])('uses the accent for fully constrained %s selections and pending constraint picks', async role => {
    const style = document.createElement('style'); style.textContent = new TextDecoder().decode(readFileSync('src/sketch/sketchCanvas.css')); document.head.append(style);
    try {
      const sketch = base();
      sketch.points.push({ id: 'r', x: 0, y: 5 }, { id: 's', x: 10, y: 5 });
      sketch.entities.push({ id: 'other', kind: 'line', p1: 'r', p2: 's' });
      const diagnosis = { ...initialDiagnosis(sketch), dof: 0 };
      await mount(sketch, diagnosis, role);
      const line = () => host.querySelector('.sketch-entity.determined')!;
      expect(getComputedStyle(line()).stroke).toBe('var(--sketch-determined)');
      await pointer(line(), 'pointerdown'); await pointer(svg(), 'pointerup');
      expect(line().classList.contains('selected')).toBe(true);
      expect(getComputedStyle(line()).stroke).toBe('var(--sketch-selected)');
      expect(getComputedStyle(line()).strokeWidth).toBe(role === 'rigid' ? '4.5' : '3.5');
      await key('Escape');
      expect(getComputedStyle(line()).stroke).toBe('var(--sketch-determined)');
      await act(async () => button('Parallel').click());
      const p = coordinate('p'), q = coordinate('q');
      await pointer(line(), 'pointermove', (p.x + q.x) / 2, (p.y + q.y) / 2, { buttons: 0 });
      expect(line().classList.contains('hovered')).toBe(true);
      expect(getComputedStyle(line()).stroke).toBe('var(--sketch-selected)');
      await pointer(line(), 'pointerdown', (p.x + q.x) / 2, (p.y + q.y) / 2); await pointer(svg(), 'pointerup');
      expect(line().classList.contains('selected')).toBe(true);
      expect(getComputedStyle(line()).stroke).toBe('var(--sketch-selected)');
      await key('Escape'); await key('Escape');
      await act(async () => button('Coincident').click());
      const point = () => host.querySelector('.sketch-point.determined[data-id="q"]')!;
      await pointer(point(), 'pointermove', q.x, q.y, { buttons: 0 });
      expect(getComputedStyle(point()).stroke).toBe('var(--sketch-selected)');
      await pointer(point(), 'pointerdown', q.x, q.y); await pointer(svg(), 'pointerup');
      expect(point().classList.contains('selected')).toBe(true);
      expect(getComputedStyle(point()).stroke).toBe('var(--sketch-selected)');
      expect(getComputedStyle(point()).strokeWidth).toBe('3.5');
    } finally { style.remove(); }
  });
  it('pans with Hand over geometry and dimension labels without selecting or editing, then Esc selects', async () => {
    const initial = base(); initial.constraints.push({ id: 'length', kind: 'distance', a: 'p', b: 'q', value: 10, driving: true });
    await mount(initial);
    const beginDrag = vi.spyOn(SketchController.prototype, 'beginDrag'), command = vi.spyOn(SketchController.prototype, 'command');
    await act(async () => button('Pan (hold Space)').click());
    expect(button('Pan (hold Space)').title).toBe('Pan (hold Space)');
    expect(svg().classList.contains('sketch-pan')).toBe(true);
    const before = coordinate('q');
    await pointer(before.element, 'pointerdown', before.x, before.y);
    expect(svg().classList.contains('grabbing')).toBe(true);
    await pointer(svg(), 'pointermove', before.x + 25, before.y + 15); await pointer(svg(), 'pointerup');
    expect(coordinate('q').x).toBeCloseTo(before.x + 25); expect(coordinate('q').y).toBeCloseTo(before.y + 15);
    const label = host.querySelector('[data-constraint-id="length"] text')!;
    const x = coordinate('q').x;
    await pointer(label, 'pointerdown', 100, 100); await pointer(svg(), 'pointermove', 110, 100); await pointer(svg(), 'pointerup');
    await pointer(label, 'dblclick'); await key('Delete');
    expect(coordinate('q').x).toBeCloseTo(x + 10);
    expect(host.querySelector('.selected')).toBeNull(); expect(host.querySelector('[aria-label="Edit dimension"]')).toBeNull();
    expect(beginDrag).not.toHaveBeenCalled(); expect(command).not.toHaveBeenCalled();
    await key('Escape'); expect(button('Select (Esc)').getAttribute('aria-pressed')).toBe('true');
  });
  it.each(['space', 'middle'] as const)('keeps %s drag panning from the Line tool even over a label', async mode => {
    const initial = base(); initial.constraints.push({ id: 'length', kind: 'distance', a: 'p', b: 'q', value: 10, driving: true });
    await mount(initial); await key('l');
    const command = vi.spyOn(SketchController.prototype, 'command'), before = coordinate('q');
    if (mode === 'space') await key(' ');
    await pointer(host.querySelector('[data-constraint-id="length"] text')!, 'pointerdown', 100, 100, { button: mode === 'middle' ? 1 : 0 });
    await pointer(svg(), 'pointermove', 130, 115); await pointer(svg(), 'pointerup');
    if (mode === 'space') await act(async () => svg().dispatchEvent(new KeyboardEvent('keyup', { key: ' ', bubbles: true })));
    expect(coordinate('q').x).toBeCloseTo(before.x + 30); expect(coordinate('q').y).toBeCloseTo(before.y + 15);
    expect(button('Line (L)').getAttribute('aria-pressed')).toBe('true'); expect(command).not.toHaveBeenCalled();
    expect(host.querySelector('.selected')).toBeNull();
  });
  it('zooms buttons and focused canvas keys 1.25 times around the centre', async () => {
    await mount(); svg().focus();
    const original = coordinate('q');
    for (const action of ['Zoom in', '+', '=', '-', 'Zoom out']) {
      const before = coordinate('q'), p = coordinate('p'), factor = action === '-' || action === 'Zoom out' ? 1 / 1.25 : 1.25;
      if (action.startsWith('Zoom')) await act(async () => button(action).click()); else await key(action);
      const after = coordinate('q'), nextP = coordinate('p');
      expect(after.x).toBeCloseTo(320 + (before.x - 320) * factor);
      expect(after.y).toBeCloseTo(180 + (before.y - 180) * factor);
      expect((after.x - nextP.x) / (before.x - p.x)).toBeCloseTo(factor);
    }
    expect(coordinate('q').x).toBeCloseTo(320 + (original.x - 320) * 1.25);
  });
  it('pans pixel scroll and uses stronger cursor-centred pinch zoom', async () => {
    await mount();
    const before = coordinate('q');
    await act(async () => svg().dispatchEvent(new WheelEvent('wheel', { deltaX: 10, deltaY: 5, deltaMode: 0, bubbles: true })));
    const scrolled = coordinate('q');
    expect(scrolled.x).toBeCloseTo(before.x - 10); expect(scrolled.y).toBeCloseTo(before.y - 5);
    for (const [deltaY, ctrlKey, factor] of [[-2, true, Math.exp(.02)], [-100, false, Math.exp(.1)]] as const) {
      const q = coordinate('q');
      await act(async () => svg().dispatchEvent(new WheelEvent('wheel', { clientX: 100, clientY: 110, deltaY, ctrlKey, bubbles: true })));
      expect(coordinate('q').x).toBeCloseTo(100 + (q.x - 100) * factor);
      expect(coordinate('q').y).toBeCloseTo(110 + (q.y - 110) * factor);
    }
  });
  it('shows every line channel in a toolbar popover and keeps projected context solid', async () => {
    const style = document.createElement('style'); style.textContent = new TextDecoder().decode(readFileSync('src/sketch/sketchCanvas.css')); document.head.append(style);
    try {
      const sketch = base();
      await act(async () => root.render(<SketchCanvas sketch={sketch} references={[]} problems={[]} context={{ fingerprint: '1', rimPlaneLabel: '', items: [
        { id: 'wall', role: 'wall', kind: 'segment', label: 'Wall', geometry: [{ x: 1, y: 2 }, { x: 5, y: 2 }], pickable: true, emphasis: 'far' },
      ] }}/>));
      const context = getComputedStyle(host.querySelector('.sketch-context-layer')!);
      expect(context.strokeDasharray).toBe('none'); expect(context.stroke).toBe('var(--sketch-context-color)'); expect(context.strokeWidth).toBe('1');
      expect(getComputedStyle(host.querySelector('.sketch-context-item.far')!).opacity).toBe('0.25');
      await act(async () => button('Line styles').click());
      const legend = host.querySelector('[aria-label="Line styles legend"]')!;
      expect(legend.textContent).toContain('Profile: under-constrained'); expect(legend.textContent).toContain('Profile: fully constrained');
      expect(legend.querySelectorAll('svg')).toHaveLength(7);
      expect(getComputedStyle(legend.querySelector('.construction')!).strokeDasharray).toBe('6 4');
      expect(getComputedStyle(legend.querySelector('.rigid')!).strokeWidth).toBe('3.5');
      await act(async () => button('Line styles').click()); expect(host.querySelector('[aria-label="Line styles legend"]')).toBeNull();
    } finally { style.remove(); }
  });
});
