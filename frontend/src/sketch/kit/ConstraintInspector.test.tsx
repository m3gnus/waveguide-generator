import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConstraintInspector } from './ConstraintInspector';
import { SketchCanvas } from '../SketchCanvas';
import { SketchController } from '../controller';
import { applyCommand, importedSketch } from '../ops';
import type { Sketch } from '../model';
import type { Diagnosis } from '../session';
import { realSessionReady } from '../testSupport';
import { readSketchView, sketchViewKey } from '../viewPreferences';

function sample(): Sketch {
  const sketch = importedSketch([{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 10, y: 0 }, { id: 'p3', x: 0, y: 5 }],
    [{ id: 'l4', kind: 'line', p1: 'p1', p2: 'p2' }]);
  sketch.constraints = [
    { id: 'c5', kind: 'horizontal', line: 'l4' },
    { id: 'c6', kind: 'distance', a: 'p1', b: 'p2', value: 10, driving: true, label: 'Width' },
    { id: 'c7', kind: 'distance', a: 'p1', b: 'p3', value: 5, driving: false },
  ];
  sketch.nextId = 8;
  return sketch;
}
let host: HTMLDivElement, root: Root;
const hover = vi.fn(), select = vi.fn(), command = vi.fn(async () => undefined);
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.removeItem(sketchViewKey); hover.mockClear(); select.mockClear(); command.mockClear();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host.appendChild(document.createElement('div')));
});
afterEach(() => { act(() => root.unmount()); host.remove(); localStorage.removeItem(sketchViewKey); });
const rows = () => [...host.querySelectorAll<HTMLElement>('[data-inspector-id]')];
const row = (id: string) => host.querySelector<HTMLElement>(`[data-inspector-id="${id}"]`)!;
const click = async (element: Element) => act(async () => (element as HTMLElement).click());
const key = async (element: Element, key: string) => act(async () => { element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })); });
async function mount(props: Partial<Parameters<typeof ConstraintInspector>[0]> = {}) {
  await act(async () => root.render(<ConstraintInspector sketch={sample()} selection={[]} enabled pending={false} open onOpenChange={() => {}}
    onHover={hover} onSelect={select} command={command} {...props}/>));
}

describe('ConstraintInspector', () => {
  it('groups dimensions first, shows values, reference tags, expressions and palette symbols', async () => {
    const sketch = sample(); sketch.constraints[1].expression = '10';
    await mount({ sketch });
    expect(rows().map(r => r.dataset.inspectorId)).toEqual(['c6', 'c7', 'c5']);
    expect(host.querySelector('h4')?.textContent).toBe('Dimensions (2)');
    expect(host.querySelectorAll('h4')[1].textContent).toBe('Relations (1)');
    expect(row('c6').textContent).toContain('10.00 mm');
    expect(row('c7').textContent).toContain('5.00 mm (ref)');
    expect(row('c6').querySelector('[title="10"]')?.textContent).toBe('ƒ');
    expect(row('c5').querySelector('.constraint-inspector-kind')?.textContent).toBe('H');
  });
  it('shows an unresolved imported expression as a broken row with the error tooltip', async () => {
    const sketch = sample(); sketch.constraints[1].expression = 'Missing*2';
    await mount({ sketch });
    const status = row('c6').querySelector('.constraint-inspector-status')!;
    expect(status.getAttribute('aria-label')).toBe('broken expression');
    expect(status.getAttribute('title')).toContain('Unknown dimension reference "Missing"');
    await click(row('c6')); expect(select).toHaveBeenLastCalledWith(['c6']);
  });
  it('filters by a selected point, curve or constraint and shows all without selection', async () => {
    await mount({ selection: ['p3'] }); expect(rows().map(r => r.dataset.inspectorId)).toEqual(['c7']);
    await mount({ selection: ['l4'] }); expect(rows().map(r => r.dataset.inspectorId)).toEqual(['c6', 'c7', 'c5']);
    await mount({ selection: ['c5'] }); expect(rows().map(r => r.dataset.inspectorId)).toEqual(['c5']);
    await mount(); expect(rows()).toHaveLength(3);
  });
  it('renders diagnosis statuses, including partial redundancy, and the analysis explanation', async () => {
    await mount({ diagnosis: { conflicting: ['c6'], redundant: ['c5'] } });
    expect(row('c6').classList.contains('conflicting')).toBe(true);
    expect(row('c5').classList.contains('redundant')).toBe(true);
    expect(row('c7').classList.contains('reference')).toBe(true);
    await mount({ diagnosis: { partiallyRedundant: ['c6'] } });
    expect(row('c6').classList.contains('redundant')).toBe(true);
    await mount(); expect(row('c6').classList.contains('notAnalysed')).toBe(true);
    expect(row('c6').querySelector('.constraint-inspector-status')?.getAttribute('title')).toContain('above 64 entities');
  });
  it('hovers and selects rows, and chips select only their item', async () => {
    await mount();
    await act(async () => row('c6').dispatchEvent(new MouseEvent('pointerover', { bubbles: true })));
    expect(hover).toHaveBeenLastCalledWith('c6');
    await act(async () => row('c6').dispatchEvent(new MouseEvent('pointerout', { bubbles: true })));
    expect(hover).toHaveBeenLastCalledWith(undefined);
    await click(row('c6')); expect(select).toHaveBeenLastCalledWith(['c6']); select.mockClear();
    await click(row('c6').querySelector('button')!); expect(select).toHaveBeenCalledExactlyOnceWith(['p1']);
    await act(async () => row('c5').focus()); expect(hover).toHaveBeenLastCalledWith('c5');
    await key(row('c5'), 'Enter'); expect(select).toHaveBeenLastCalledWith(['c5']);
  });
  it('issues one command per delete or driving action, isolates child keys and disables pending writes', async () => {
    await mount();
    await key(row('c5'), 'Delete'); expect(command).toHaveBeenCalledExactlyOnceWith({ kind: 'removeConstraint', id: 'c5' });
    command.mockClear(); await key(row('c6'), 'Backspace'); expect(command).toHaveBeenCalledExactlyOnceWith({ kind: 'removeConstraint', id: 'c6' });
    command.mockClear(); await click(row('c6').querySelector('[aria-label^="Make reference"]')!);
    expect(command).toHaveBeenCalledExactlyOnceWith({ kind: 'setDriving', id: 'c6', driving: false });
    command.mockClear(); await click(row('c7').querySelector('[aria-label^="Make driving"]')!);
    expect(command).toHaveBeenCalledExactlyOnceWith({ kind: 'setDriving', id: 'c7', driving: true });
    command.mockClear(); await key(row('c6').querySelector('button')!, 'Delete'); expect(command).not.toHaveBeenCalled();
    await mount({ pending: true }); await key(row('c5'), 'Delete');
    expect(row('c5').querySelector<HTMLButtonElement>('[aria-label^="Delete"]')?.disabled).toBe(true);
    expect(command).not.toHaveBeenCalled();
  });
  it('explains empty sketches and unconstrained selections', async () => {
    await mount({ sketch: { ...sample(), constraints: [] } });
    expect(host.textContent).toContain('No constraints yet. Add one from the Constraints toolbar.');
    await mount({ selection: ['unknown'] }); expect(host.textContent).toContain('Nothing constrains the selection.');
  });
  it('uses inspector formatting for diameter and angle dimensions', async () => {
    const sketch = sample();
    sketch.entities.push({ id: 'a8', kind: 'arc', center: 'p1', start: 'p2', end: 'p3', ccw: true });
    sketch.constraints = [{ id: 'c9', kind: 'radius', arc: 'a8', value: 10, driving: true, diameter: true },
      { id: 'c10', kind: 'angle', a: 'l4', b: 'l4', value: Math.PI / 2, driving: true }];
    await mount({ sketch }); expect(row('c9').textContent).toContain('Ø20.00 mm'); expect(row('c10').textContent).toContain('90.00°');
  });
});

let current: Sketch;
function CanvasHarness({ initial, properties }: { initial: Sketch; properties: HTMLElement }) {
  const [sketch, setSketch] = useState(initial), [diagnosis, setDiagnosis] = useState<Diagnosis>();
  const [session, setSession] = useState<SketchController<null>>();
  useEffect(() => {
    const controller = new SketchController({ initial: { sketch: initial, meta: null }, references: [], apply: (state, command) => {
      const result = applyCommand(state.sketch, command); return 'error' in result ? result : { ...state, sketch: result.sketch };
    }, changed: (state, result) => { current = state.sketch; setSketch(state.sketch); setDiagnosis(result); setSession(controller); } });
    return () => controller.destroy();
  }, [initial]);
  return <SketchCanvas sketch={sketch} diagnosis={diagnosis} session={session} references={[]} problems={[]} slots={{ properties }}/>;
}
async function canvas() {
  await realSessionReady();
  const properties = document.createElement('div'); host.append(properties);
  await act(async () => root.render(<CanvasHarness initial={sample()} properties={properties}/>));
  return properties;
}
it('mounts in the properties slot, highlights the canvas, selects chips, and deletes with one undo step', async () => {
  const properties = await canvas();
  expect(properties.querySelector('.constraint-inspector')).not.toBeNull();
  await act(async () => row('c5').dispatchEvent(new MouseEvent('pointerover', { bubbles: true })));
  expect(host.querySelector('[data-constraint-id="c5"] .sketch-glyph')?.classList.contains('selected')).toBe(true);
  expect(host.querySelector('.sketch-entity[data-id="l4"]')?.classList.contains('selected')).toBe(true);
  await act(async () => row('c5').dispatchEvent(new MouseEvent('pointerout', { bubbles: true })));
  expect(host.querySelector('.sketch-entity[data-id="l4"]')?.classList.contains('selected')).toBe(false);
  await click(row('c6').querySelector('button')!);
  expect(host.querySelector('.sketch-point[data-id="p1"]')?.classList.contains('selected')).toBe(true);
  await key(row('c6'), 'Enter'); expect(rows().map(r => r.dataset.inspectorId)).toEqual(['c6']);
  await key(row('c6'), 'Delete'); expect(current.constraints.map(c => c.id)).toEqual(['c5', 'c7']);
  const undo = [...host.querySelectorAll('button')].find(b => b.textContent === 'Undo')!;
  await click(undo); expect(current.constraints.map(c => c.id)).toEqual(['c5', 'c6', 'c7']);
  await click(undo); expect(current.constraints.map(c => c.id)).toEqual(['c5', 'c6', 'c7']);
});
it('toggles driving through the session command path and undoes once', async () => {
  await canvas(); await click(row('c6').querySelector('[aria-label^="Make reference"]')!);
  expect(current.constraints.find(c => c.id === 'c6')).toMatchObject({ driving: false });
  expect(row('c6').textContent).toContain('(ref)');
  await click([...host.querySelectorAll('button')].find(b => b.textContent === 'Undo')!);
  expect(current.constraints.find(c => c.id === 'c6')).toMatchObject({ driving: true });
  await click(row('c7').querySelector('[aria-label^="Make driving"]')!);
  expect(current.constraints.find(c => c.id === 'c7')).toMatchObject({ driving: true });
});
it('persists collapse state in sketch view preferences across remounts', async () => {
  await canvas(); const details = host.querySelector<HTMLDetailsElement>('.constraint-inspector')!;
  await act(async () => { details.open = false; details.dispatchEvent(new Event('toggle')); });
  expect(readSketchView().inspectorOpen).toBe(false);
  await act(async () => root.render(null)); await canvas();
  expect(host.querySelector<HTMLDetailsElement>('.constraint-inspector')?.open).toBe(false);
});
