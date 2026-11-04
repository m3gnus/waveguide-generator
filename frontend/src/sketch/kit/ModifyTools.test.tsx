import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SketchController } from '../controller';
import { importedSketch, applyCommand } from '../ops';
import type { Sketch } from '../model';
import type { Diagnosis } from '../session';
import type { Reference } from '../adapter';
import type { SketchContext } from '../context';
import { refreshContext } from '../context';
import { realSessionReady } from '../testSupport';
import { sourceDisplay } from '../adapters/sourceDisplay';
import { displayPath } from '../workspace/WorkspaceItems';
import { extendPreview, filletPreview, trimPreview } from '../topology';
import { SketchKit } from './sketchKit';
import { trimmedArc } from '../bendTestSupport';
import { arcGeometry, distance } from '../geometry';
import { modifyContext } from '../tools/modifyContext';

let host: HTMLDivElement, root: Root, current: Sketch, controller: SketchController<null>;
const view = { scale: 20, offsetX: 100, offsetY: 320 };
const context: SketchContext = { fingerprint: 'test', rimPlaneLabel: '', items: [{ id: 'wall', kind: 'polyline', geometry: [{ x: 11, y: 1 }, { x: 11, y: 12 }], role: 'wall', label: 'Horn wall', pickable: false }] };
function corner() {
  const s = importedSketch([{ id: 'p1', x: 4, y: 14 }, { id: 'p2', x: 4, y: 4 }, { id: 'p3', x: 14, y: 4 }],
    [{ id: 'l4', kind: 'line', p1: 'p1', p2: 'p2' }, { id: 'l5', kind: 'line', p1: 'p2', p2: 'p3' }]);
  s.constraints.push({ id: 'c6', kind: 'fix', point: 'p2' }); s.nextId = 7; return s;
}
function lines(withCutters = true) {
  return importedSketch([
    { id: 'p1', x: 4, y: 4 }, { id: 'p2', x: 14, y: 4 }, { id: 'p3', x: 4, y: 8 }, { id: 'p4', x: 14, y: 8 },
    { id: 'p5', x: 7, y: 1 }, { id: 'p6', x: 7, y: 12 }, { id: 'p7', x: 11, y: 1 }, { id: 'p8', x: 11, y: 12 },
  ], [
    { id: 'l9', kind: 'line', p1: 'p1', p2: 'p2' }, { id: 'l10', kind: 'line', p1: 'p3', p2: 'p4' },
    ...(withCutters ? [{ id: 'l11', kind: 'line' as const, p1: 'p5', p2: 'p6', reference: true, construction: true }, { id: 'l12', kind: 'line' as const, p1: 'p7', p2: 'p8', reference: true, construction: true }] : []),
  ]);
}
function Harness({ initial, selected = [], context, references = [] }: { initial: Sketch; selected?: string[]; context?: SketchContext; references?: Reference[] }) {
  const [sketch, setSketch] = useState(initial), [diagnosis, setDiagnosis] = useState<Diagnosis>();
  const [session, setSession] = useState<SketchController<null>>();
  const [selection, setSelection] = useState(selected);
  useEffect(() => {
    const c = new SketchController({ initial: { sketch: initial, meta: null }, references, apply: (s, command) => {
      const result = applyCommand(s.sketch, command); return 'error' in result ? result : { ...s, sketch: result.sketch };
    }, changed: (s, d) => { current = s.sketch; setSketch(s.sketch); setDiagnosis(d); setSession(c); } });
    controller = c; return () => c.destroy();
  }, [initial]);
  return <><SketchKit sketch={sketch} session={session} diagnosis={diagnosis} references={references} problems={[]} context={context} selection={selection} onSelectionChange={setSelection}
    presentation={{ axes: { x: { label: 'x', unit: 'mm' }, y: { label: 'y', unit: 'mm' } }, initialView: () => view, display: (sketch, ui) => sourceDisplay({ sketch, meta: { references } }, ui) }}/>{session?.notice && <p role="status" className="test-notice">{session.notice}</p>}</>;
}
beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  view.scale = 20; localStorage.clear(); await realSessionReady(); host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.restoreAllMocks(); });
async function mount(initial: Sketch, selected?: string[], ctx?: SketchContext, references?: Reference[]) { current = initial; await act(async () => root.render(<Harness initial={initial} selected={selected} context={ctx} references={references}/>)); }
const svg = () => host.querySelector<SVGSVGElement>('svg[role="application"]')!;
const field = () => host.querySelector<HTMLInputElement>('input[aria-label="Fillet radius"]');
const button = (label: string) => [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') ?? b.textContent) === label)!;
async function key(key: string, target: Element = svg(), options = {}) { await act(async () => { target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options })); }); }
async function pointer(type: string, x: number, y: number, buttons = type === 'pointerup' ? 0 : 1) {
  await act(async () => { svg().dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: view.offsetX + x * view.scale, clientY: view.offsetY - y * view.scale, buttons })); });
}
async function click(x: number, y: number) { await pointer('pointerdown', x, y); await pointer('pointerup', x, y); }
async function value(text: string) { await act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field(), text);
  field()!.dispatchEvent(new Event('input', { bubbles: true }));
}); }
const preview = (tool: string) => host.querySelector<SVGPathElement>(`[aria-label="${tool} preview"]`);
const pressed = (label: string) => expect(button(label).getAttribute('aria-pressed')).toBe('true');
async function undo() { await act(async () => button('Undo').click()); }

describe('Modify toolbar and keyboard', () => {
  it.each([['g', 'Fillet (G)'], ['t', 'Trim (T)'], ['e', 'Extend (E)']])('activates %s, preserves Fit, and Esc returns to Select', async (shortcut, label) => {
    await mount(corner());
    const groups = [...host.querySelectorAll('.sketch-toolbar > [role="group"]')].map(g => g.getAttribute('aria-label'));
    expect(groups.slice(0, 4)).toEqual(['Navigate', 'Sketch', 'Modify', 'Constraints']);
    await key(shortcut); pressed(label); expect(button('Fit (F)')).toBeDefined();
    await key('Escape'); pressed('Select (Esc)');
    await act(async () => button(label).click()); pressed(label);
  });
});

describe('Fillet', () => {
  it('highlights a corner, previews the exact arc while typing, applies a radius dimension, shows notice, and undoes', async () => {
    await mount(corner()); const before = current;
    await key('g'); await pointer('pointermove', 4, 4, 0);
    expect(host.querySelector('[aria-label="Fillet corner"]')).not.toBeNull(); expect(field()).toBeNull();
    await click(4, 4); expect(field()!.value).toBe('2'); expect(document.activeElement).toBe(field());
    const arc = filletPreview(before, 'p2', 2); if ('error' in arc) throw new Error(arc.error);
    expect(preview('fillet')!.getAttribute('d')).toBe(displayPath({ id: '', role: '', editable: false, kind: 'arc', ...arc }, view));
    const first = preview('fillet')!.getAttribute('d'); await value('3,5'); expect(preview('fillet')!.getAttribute('d')).not.toBe(first);
    await key('Enter', field()!); expect(field()).toBeNull(); pressed('Fillet (G)');
    expect(current.constraints).toContainEqual(expect.objectContaining({ kind: 'radius', value: 3.5, driving: true }));
    expect(host.querySelector('.sketch-dimension')).not.toBeNull(); expect(host.querySelector('.sketch-status')!.textContent).not.toContain('removed'); expect(controller.notice).toContain('Fix');
    await undo(); expect(current).toEqual(before);
    await key('g'); await click(4, 4); expect(field()!.value).toBe('3,5');
  });
  it('highlights and fillets a corner joined by a normal endpoint snap, with one undo step', async () => {
    const s = corner(); s.entities = s.entities.slice(0, 1); s.points = s.points.slice(0, 2); s.constraints = [];
    const drawn = applyCommand(s, { kind: 'addLine', from: { x: 4, y: 4, snap: { glyph: 'endpoint', x: 4, y: 4, constraint: { kind: 'coincident', point: 'p2' } } }, to: { x: 14, y: 4 } });
    if ('error' in drawn) throw new Error(drawn.error);
    await mount(drawn.sketch); const before = current;
    await key('g'); await pointer('pointermove', 4, 4, 0);
    expect(host.querySelector('[aria-label="Fillet corner"]')).not.toBeNull();
    await click(4, 4); expect(preview('fillet')).not.toBeNull();
    await key('Enter', field()!);
    expect(current.entities.some(e => e.kind === 'arc')).toBe(true);
    expect(controller.notice).toContain('Coincident');
    await undo(); expect(current).toEqual(before); expect(controller.history.canUndo).toBe(false);
  });
  it('opens on preselection and cancels through the owned Escape handler without editing', async () => {
    await mount(corner(), ['p2']); const before = current; await key('g'); expect(field()).not.toBeNull();
    await key('Escape', field()!); expect(field()).toBeNull(); expect(preview('fillet')).toBeNull(); pressed('Select (Esc)'); expect(current).toBe(before);
  });
  it('lets the Cancel button own Enter without submitting the fillet', async () => {
    await mount(corner()); await key('g'); await click(4, 4); const command = vi.spyOn(controller, 'command');
    const cancel = [...host.querySelectorAll('form[aria-label="Fillet radius"] button')].find(b => b.textContent === 'Cancel') as HTMLButtonElement;
    await key('Enter', cancel); expect(command).not.toHaveBeenCalled();
    await act(async () => cancel.click()); expect(field()).toBeNull(); pressed('Select (Esc)');
  });
  it('does not reopen a cancelled prompt after a late command rejection', async () => {
    await mount(corner()); await key('g'); await click(4, 4);
    let reject!: (reason: Error) => void;
    vi.spyOn(controller, 'command').mockImplementationOnce(() => new Promise((_, no) => { reject = no; }));
    await key('Enter', field()!); await key('Escape', field()!); pressed('Select (Esc)');
    await act(async () => reject(new Error('Move the fixed endpoint.')));
    expect(field()).toBeNull(); pressed('Select (Esc)');
  });
  it('shows preview guidance for too-large radii and non-corners', async () => {
    await mount(corner()); await key('g'); await click(4, 4); await value('100');
    expect(host.querySelector('[role="alert"]')!.textContent).toBe((filletPreview(current, 'p2', 100) as { error: string }).error);
    expect(preview('fillet')).toBeNull(); await key('Enter', field()!); expect(field()).not.toBeNull();
    await key('Escape', field()!); await key('g'); await click(4, 14);
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('exactly two');
  });
  it('retains the prompt and shows a failed command under the field', async () => {
    await mount(corner()); await key('g'); await click(4, 4);
    vi.spyOn(controller, 'command').mockRejectedValueOnce(new Error('Move the fixed endpoint before rounding.'));
    await key('Enter', field()!); expect(field()).not.toBeNull(); expect(host.querySelector('[role="alert"]')!.textContent).toContain('Move the fixed endpoint');
  });
});

describe('Trim', () => {
  it('previews exactly the removed interval, applies, stays active, and undoes', async () => {
    await mount(lines()); const before = current; await key('t'); await pointer('pointermove', 9, 4, 0);
    expect(trimPreview(current, 'l9', { x: 9, y: 4 })).toEqual({ kind: 'line', a: { x: 7, y: 4 }, b: { x: 11, y: 4 } });
    expect(preview('trim')!.getAttribute('d')).toBe('M240,240 L320,240'); expect(preview('trim')!.classList.contains('sketch-trim-preview')).toBe(true);
    await click(9, 4); expect(current.entities.filter(e => !e.reference)).toHaveLength(3); pressed('Trim (T)');
    await undo(); expect(current).toEqual(before);
  });
  it('trims multiple crossed curves between sparse pointer events as one undoable command array', async () => {
    await mount(lines()); const before = current, command = vi.spyOn(controller, 'command');
    await key('t'); await pointer('pointerdown', 9, 2); await pointer('pointermove', 9, 10); expect(current).toBe(before);
    await pointer('pointerup', 9, 10); expect(command).toHaveBeenCalledTimes(1);
    expect(command.mock.calls[0][0]).toEqual([expect.objectContaining({ kind: 'trim', entity: 'l9' }), expect.objectContaining({ kind: 'trim', entity: 'l10' })]);
    expect(current.entities.filter(e => !e.reference)).toHaveLength(4); await undo(); expect(current).toEqual(before);
    await key('z', svg(), { ctrlKey: true, shiftKey: true }); expect(current.entities.filter(e => !e.reference)).toHaveLength(4);
  });
  it('trims both curves at an X crossing rather than repicking a surviving endpoint', async () => {
    const s = importedSketch([{ id: 'p1', x: 4, y: 8 }, { id: 'p2', x: 14, y: 8 }, { id: 'p3', x: 9, y: 3 }, { id: 'p4', x: 9, y: 13 }],
      [{ id: 'l5', kind: 'line', p1: 'p1', p2: 'p2' }, { id: 'l6', kind: 'line', p1: 'p3', p2: 'p4' }]);
    await mount(s); const before = current, command = vi.spyOn(controller, 'command'); await key('t');
    await pointer('pointerdown', 8, 7); await pointer('pointermove', 10, 9); await pointer('pointerup', 10, 9);
    expect(command.mock.calls[0][0]).toEqual([expect.objectContaining({ entity: 'l5' }), expect.objectContaining({ entity: 'l6' })]);
    expect(current.entities.filter(e => !e.reference)).toHaveLength(2);
    await undo(); expect(current).toEqual(before);
  });
  it('trims both crossed spans of an arc during one gesture', async () => {
    const s = importedSketch([{ id: 'p1', x: 10, y: 6 }, { id: 'p2', x: 14, y: 6 }, { id: 'p3', x: 6, y: 6 }, { id: 'p4', x: 10, y: 1 }, { id: 'p5', x: 10, y: 12 }],
      [{ id: 'a6', kind: 'arc', center: 'p1', start: 'p2', end: 'p3', ccw: true }, { id: 'l7', kind: 'line', p1: 'p4', p2: 'p5', reference: true }]);
    await mount(s); const before = current, command = vi.spyOn(controller, 'command'); await key('t');
    await pointer('pointerdown', 5, 8); await pointer('pointermove', 15, 8); await pointer('pointerup', 15, 8);
    expect(command.mock.calls[0][0]).toHaveLength(2); expect(current.entities.some(e => e.kind === 'arc')).toBe(false);
    await undo(); expect(current).toEqual(before);
  });
  it('cancels a drag with Esc or pointer cancellation without applying', async () => {
    await mount(lines()); const before = current;
    for (const cancel of ['Escape', 'pointercancel']) {
      await key('t'); await pointer('pointerdown', 9, 2); await pointer('pointermove', 9, 10);
      if (cancel === 'Escape') await key(cancel); else await pointer(cancel, 9, 10);
      await pointer('pointerup', 9, 10); expect(current).toBe(before);
    }
  });
  it('hides a hover preview on Esc', async () => {
    await mount(lines()); await key('t'); await pointer('pointermove', 9, 4, 0); expect(preview('trim')).not.toBeNull();
    await key('Escape'); expect(preview('trim')).toBeNull(); pressed('Select (Esc)');
  });
  it('surfaces command errors in the existing status line', async () => {
    await mount(lines()); await key('t'); vi.spyOn(controller, 'command').mockRejectedValueOnce(new Error('Choose another piece to remove.'));
    await click(9, 4); expect(host.querySelector('.sketch-status')!.textContent).toContain('Choose another piece');
  });
  it('shows a removed-constraint notice', async () => {
    const s = lines(false); s.constraints.push({ id: 'c20', kind: 'fix', point: 'p1' }); s.nextId = 21;
    await mount(s); await key('t'); await click(9, 4); expect(host.querySelector('.sketch-status')!.textContent).not.toContain('removed'); expect(controller.notice).toContain('Fix');
    expect(host.textContent!.split(controller.notice!).length - 1).toBe(1);
    expect(host.querySelector('.test-notice')!.textContent).toBe('Trim removed 1 relation: Fix.');
  });
});

describe('Extend and protected targets', () => {
  it('previews the exact extension, applies, stays active, and undoes', async () => {
    const s = lines(); s.points[0].x = 8; s.points[1].x = 9;
    s.constraints.push({ id: 'c13', kind: 'fix', point: 'p2' }); s.nextId = 14;
    await mount(s); const before = current; await key('e'); await pointer('pointermove', 9, 4, 0);
    expect(extendPreview(current, 'l9', { x: 9, y: 4 })).toEqual({ kind: 'line', from: expect.objectContaining({ x: 9, y: 4 }), to: { x: 11, y: 4 } });
    expect(preview('extend')!.getAttribute('d')).toBe('M280,240 L320,240');
    await key('Escape'); expect(preview('extend')).toBeNull(); await key('e'); await click(9, 4);
    expect(current.points.find(p => p.id === 'p2')!.x).toBeCloseTo(11); expect(host.querySelector('.sketch-status')!.textContent).not.toContain('removed'); expect(controller.notice).toContain('Fix'); pressed('Extend (E)'); await undo(); expect(current).toEqual(before);
  });
  it('shows the preview failure and empty-hover guidance; Esc hides the preview', async () => {
    await mount(lines(false)); await key('e'); await pointer('pointermove', 14, 4, 0);
    expect(preview('extend')).toBeNull(); expect(host.querySelector('.sketch-status')!.textContent).toContain('No curve is hit beyond');
    await pointer('pointermove', 23, 14, 0); expect(host.querySelector('.sketch-status')!.textContent).toContain('No editable curve nearby');
    await key('Escape'); pressed('Select (Esc)');
  });
  it.each(['g', 't', 'e'])('never modifies reference targets with %s', async shortcut => {
    await mount(lines()); const before = current; await key(shortcut); await pointer('pointermove', 7, 10, 0);
    expect(preview('trim')).toBeNull(); expect(preview('extend')).toBeNull(); expect(host.querySelector('[aria-label="Fillet corner"]')).toBeNull();
    await click(7, 10); expect(current).toBe(before);
  });
  it.each(['axis', 'baffle', 'limit'] as const)('uses the unbounded %s reference as a cutter without moving its stored endpoints', async kind => {
    const s = importedSketch([{ id: 'p1', x: 4, y: 4 }, { id: 'p2', x: 9, y: 4 }, { id: 'p3', x: 11, y: 0 }, { id: 'p4', x: 11, y: 1 }],
      [{ id: 'l5', kind: 'line', p1: 'p1', p2: 'p2' }, { id: 'l6', kind: 'line', p1: 'p3', p2: 'p4', reference: true, construction: true }]);
    const reference: Reference = kind === 'limit' ? { id: 'l6', kind, label: 'Limit', radius: 11, locked: false } : { id: 'l6', kind };
    await mount(s, [], undefined, [reference]); const before = current; await key('e'); await pointer('pointermove', 9, 4, 0);
    expect(preview('extend')!.getAttribute('d')).toBe('M280,240 L320,240'); await click(9, 4);
    expect(current.points.find(p => p.id === 'p2')!.x).toBeCloseTo(11);
    expect(current.points.filter(p => p.id === 'p3' || p.id === 'p4')).toEqual(before.points.filter(p => p.id === 'p3' || p.id === 'p4'));
    await undo(); expect(current).toEqual(before);
  });
  it('trims with an unbounded datum while keeping its saved geometry', async () => {
    const s = lines(false); s.entities.push({ id: 'l13', kind: 'line', p1: 'p5', p2: 'p6', reference: true }); s.nextId = 14;
    s.points = s.points.map(p => p.id === 'p5' ? { ...p, y: 0 } : p.id === 'p6' ? { ...p, y: 1 } : p);
    await mount(s, [], undefined, [{ id: 'l13', kind: 'axis' }]); const before = current; await key('t'); await pointer('pointermove', 6, 4, 0);
    expect(preview('trim')!.getAttribute('d')).toBe('M180,240 L240,240'); await click(6, 4);
    const line = current.entities.find(e => e.id === 'l9')!; if (line.kind !== 'line') throw new Error('Expected line');
    expect(current.points.find(p => p.id === line.p1)!.x).toBeCloseTo(7); await undo(); expect(current).toEqual(before);
  });
  it('uses display-only horn walls as targets, persists them in the same action, and refreshes/undoes them', async () => {
    const s = lines(false); s.points[1].x = 9;
    await mount(s, [], context); const before = current; await key('e'); await pointer('pointermove', 9, 4, 0);
    expect(preview('extend')!.getAttribute('d')).toBe('M280,240 L320,240'); await click(9, 4);
    expect(current.points.find(p => p.id === 'p2')!.x).toBeCloseTo(11); expect(current.entities.some(e => e.reference)).toBe(true);
    expect(refreshContext(current, context).missing).toEqual([]);
    await key('t'); await pointer('pointermove', 11, 10, 0); expect(preview('trim')).toBeNull();
    await undo(); expect(current).toEqual(before);
  });
  it('uses display-only context cutters for trim with matching preview and one-step undo', async () => {
    await mount(lines(false), [], context); const before = current; await key('t'); await pointer('pointermove', 13, 4, 0);
    expect(preview('trim')!.getAttribute('d')).toBe('M320,240 L380,240'); await click(13, 4);
    const line = current.entities.find(e => e.id === 'l9')!; if (line.kind !== 'line') throw new Error('Expected line');
    expect(current.points.find(p => p.id === line.p2)!.x).toBeCloseTo(11); await undo(); expect(current).toEqual(before);
  });
});

it('materializes context without changing input and refreshes polyline support geometry', () => {
  const initial = lines(false), prepared = modifyContext(initial, context);
  expect(initial.entities.some(e => e.reference)).toBe(false); expect(prepared.entities.some(e => e.reference)).toBe(true);
  const changed = { ...context, items: context.items.map(i => ({ ...i, geometry: i.geometry.map(p => ({ ...p, x: 12 })) })) };
  const refreshed = refreshContext(prepared, changed); expect(refreshed.missing).toEqual([]); expect(refreshed.moved).toBe(true);
});


it.each([{ dx: 0, direction: 1 }, { dx: 0, direction: -1 }, { dx: .3, direction: 1 }])('bends a trimmed stub after the four-pixel threshold ($dx, $direction), releases, and undoes', async ({ dx, direction }) => {
  const { sketch, references, arc } = trimmedArc(dx);
  if (dx) view.scale = 80;
  await mount(sketch, [], undefined, references);
  const before = current, g = arcGeometry(before, arc), angle = g.start + g.sweep / 2;
  expect(g.radius).toBeCloseTo(2.57, 2);
  const body = { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
  await pointer('pointerdown', body.x, body.y);
  for (let i = 1; i <= 30; i++) {
    await pointer('pointermove', body.x + direction * i * .01 * Math.cos(angle), body.y + direction * i * .01 * Math.sin(angle));
    await act(async () => { await new Promise<void>(resolve => requestAnimationFrame(() => resolve())); });
  }
  expect(host.querySelector('.sketch-status')!.textContent).not.toContain('Limited by constraints');
  await pointer('pointerup', body.x, body.y);
  const after = arcGeometry(current, arc);
  expect(Math.abs(after.radius - g.radius)).toBeGreaterThan(.1);
  for (const id of [arc.start, arc.end]) expect(distance(before.points.find(p => p.id === id)!, current.points.find(p => p.id === id)!)).toBeLessThan(.001);
  await undo(); expect(current).toEqual(before);
}, 20000);


it('shows chord-clamp guidance once the bend reaches the chord and clears it on dragging back', async () => {
  const { sketch, references, arc } = trimmedArc(0);
  await mount(sketch, [], undefined, references);
  const g = arcGeometry(current, arc), angle = g.start + g.sweep / 2;
  const body = { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
  const frame = async () => act(async () => { await new Promise<void>(resolve => requestAnimationFrame(() => resolve())); });
  await pointer('pointerdown', body.x, body.y);
  for (const x of [5, 4.8]) {
    await pointer('pointermove', x, body.y); await frame();
    expect(host.querySelector('.sketch-status')!.textContent).toContain("An arc can't bend past its chord · drag back");
    expect(host.querySelector('.sketch-status')!.textContent).not.toContain('Limited by constraints');
  }
  await pointer('pointermove', body.x + .1, body.y); await frame();
  expect(host.querySelector('.sketch-status')!.textContent).not.toContain("can't bend past");
  await pointer('pointerup', body.x + .1, body.y);
}, 20000);
