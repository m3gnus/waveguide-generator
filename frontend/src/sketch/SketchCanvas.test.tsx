import { act, useEffect, useRef, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SketchCanvas } from './SketchCanvas';
import { SketchController } from './controller';
import { applyCommand, importedSketch } from './ops';
import type { Sketch } from './model';
import type { Diagnosis } from './session';
import { initialDiagnosis, realSessionReady } from './testSupport';
import { readFileSync } from 'node:fs';
import { seed } from './adapters/sourceContour';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';

let host: HTMLDivElement, root: Root;
const base = () => importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }], [{ kind: 'line', id: 'line', p1: 'p', p2: 'q' }]);
function Harness({ initial = base(), diagnosis }: { initial?: Sketch; diagnosis?: Diagnosis }) {
  const [state, setState] = useState(initial), [result, setResult] = useState(diagnosis);
  const [session, setSession] = useState<SketchController<null>>();
  const handle = useRef<SketchController<null> | undefined>(undefined);
  useEffect(() => {
    const controller = new SketchController({ sketch: initial, meta: null }, [], (s, c) => { const r = applyCommand(s.sketch, c); return 'error' in r ? r : { ...s, sketch: r.sketch }; }, (s, d) => { setState(s.sketch); setResult(diagnosis ?? d); setSession(controller); }, undefined, undefined, (s, d) => { setState(s.sketch); setResult(diagnosis ?? d); });
    handle.current = controller; return () => controller.destroy();
  }, [initial, diagnosis]);
  return <SketchCanvas sketch={state} diagnosis={result} session={session} references={[]} problems={[]}/>;
}
beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  await realSessionReady();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const button = (label: string) => [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') ?? b.textContent) === label)!;
const svg = () => host.querySelector<SVGSVGElement>('svg[role="application"]')!;
const key = async (key: string, options = {}) => act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...options })); });
const pointer = async (element: Element, type: string, x = 0, y = 0, options = {}) => act(async () => { element.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, ...options })); });
async function mount(initial = base(), diagnosis?: Diagnosis) {
  await act(async () => root.render(<Harness initial={initial} diagnosis={diagnosis}/>));
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
}
function coordinate(id: string) { const p = host.querySelector(`[data-id="${id}"]`)!; return { element: p, x: Number(p.getAttribute('cx')), y: Number(p.getAttribute('cy')) }; }

describe('SketchCanvas with real slvs', () => {
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
  it('shows only selection-valid actions and keeps dimension controls contextual', async () => {
    await mount();
    expect(host.querySelector('[aria-label="Selection constraints"]')).toBeNull();
    expect(host.querySelector('[aria-label="Dimension measurement"]')).toBeNull();
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    const labels = () => [...host.querySelectorAll('[aria-label="Selection constraints"] button')].map(b => b.textContent);
    expect(labels()).toEqual(['Horizontal', 'Vertical', 'Construction (X)', 'Delete selection']);
    expect(host.querySelector('.sketch-surface > [aria-label="Selection constraints"]')).not.toBeNull();
    await pointer(svg(), 'pointerdown');
    await pointer(coordinate('p').element, 'pointerdown'); await pointer(svg(), 'pointerup');
    expect(labels()).toEqual(['Fix', 'Delete selection']);
    await pointer(coordinate('q').element, 'pointerdown', 0, 0, { shiftKey: true }); await pointer(svg(), 'pointerup');
    expect(labels()).toEqual(['Horizontal', 'Vertical', 'Coincident', 'Delete selection']);
    await act(async () => button('Dimension (D)').click());
    expect(host.querySelector('[aria-label="Dimension measurement"]')).not.toBeNull();
    await key('Escape');
    expect(host.querySelector('[aria-label="Dimension measurement"]')).toBeNull();
  });
  it('keeps the canvas offset and flow siblings unchanged when dimension options appear', async () => {
    const style = document.createElement('style');
    style.textContent = new TextDecoder().decode(readFileSync('src/sketch/sketchCanvas.css'));
    document.head.append(style);
    try {
      await mount();
      const surface = host.querySelector<HTMLElement>('.sketch-surface')!;
      const siblings = [...surface.parentElement!.children];
      const offset = svg().getBoundingClientRect().top;
      await act(async () => button('Dimension (D)').click());
      const options = host.querySelector<HTMLElement>('.sketch-dimension-context')!;
      expect(options.parentElement).toBe(surface);
      expect(getComputedStyle(options).position).toBe('absolute');
      expect([...surface.parentElement!.children]).toEqual(siblings);
      expect(svg().getBoundingClientRect().top).toBe(offset);
      expect(button('Apply dimension')).toBeUndefined();
      await key('Escape'); expect(svg().getBoundingClientRect().top).toBe(offset);
    } finally { style.remove(); }
  });
  it('adds a measured dimension in one click, selects its focused value and keeps it on Esc', async () => {
    await mount(); await act(async () => button('Dimension (D)').click());
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    const input = host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')!;
    expect(input.value).toBe('10'); expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0); expect(input.selectionEnd).toBe(2);
    await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    expect(host.querySelector('[aria-label="Dimension value"]')).toBeNull();
    expect(host.querySelector('.sketch-glyph')?.textContent).toContain('10.00 mm');
    // The same measured length becomes a reference dimension when already driven.
    await act(async () => button('Dimension (D)').click());
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown');
    expect(host.querySelector('.sketch-glyph.reference')?.textContent).toContain('(10.00 mm)');
    expect(document.activeElement).toBe(host.querySelector('[aria-label="Dimension value"]'));
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
    expect(host.querySelector('[aria-label^="Dimension distance"]')).not.toBeNull();
    expect(document.activeElement).toBe(host.querySelector('[aria-label="Dimension value"]'));
    await key('Escape'); await key('d');
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown', 0, 0, { altKey: true });
    await pointer(host.querySelector('[data-id="other"]')!, 'pointerdown', 0, 0, { altKey: true });
    expect(host.querySelector('[aria-label^="Dimension angle"]')).not.toBeNull();
    expect(host.querySelector<HTMLInputElement>('[aria-label="Dimension value"]')!.value).toBe('90');
    await key('Escape'); await key('d');
    await pointer(host.querySelector('[data-id="arc"]')!, 'pointerdown');
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
  it('renders infinite references without point or curve hit targets', async () => {
    const { sketch, meta } = seed(INITIAL_SOURCE);
    await act(async () => root.render(<SketchCanvas sketch={sketch} references={meta.references} problems={[]}/>));
    const refs = sketch.entities.filter(e => meta.references.some(r => r.id === e.id));
    const refPoints = refs.flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : []);
    for (const id of [...refPoints, ...refs.map(e => e.id)]) expect(host.querySelector(`[data-id="${id}"]`)).toBeNull();
    expect(host.querySelectorAll('.sketch-references line')).toHaveLength(2);
    expect(host.querySelector('.sketch-references line')?.getAttribute('y2')).toBe('360');
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
  it('fix 11: explicit Alt two-line picks add only an angle without an extra length', async () => {
    const initial = importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }, { id: 'r', x: 10, y: 10 }], [
      { kind: 'line', id: 'line', p1: 'p', p2: 'q' }, { kind: 'line', id: 'other', p1: 'q', p2: 'r' },
    ]);
    await mount(initial); await key('d');
    await pointer(host.querySelector('[data-id="line"]')!, 'pointerdown', 0, 0, { altKey: true });
    expect(host.querySelector('.sketch-glyph')).toBeNull();
    await pointer(host.querySelector('[data-id="other"]')!, 'pointerdown', 0, 0, { altKey: true });
    expect(host.querySelectorAll('.sketch-glyph')).toHaveLength(1);
    expect(host.querySelector('[aria-label^="Dimension angle"]')).not.toBeNull();
  });

});
