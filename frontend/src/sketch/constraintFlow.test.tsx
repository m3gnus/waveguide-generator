import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SketchCanvas, type CanvasSession } from './SketchCanvas';
import { applyCommand, importedSketch } from './ops';
import { capabilities } from './solver/common';
import { constraintFor, rules, type SelectionConstraint } from './tools/constraints';
import { materialiseContext, type SketchContext } from './context';
import type { Sketch } from './model';
import type { XY } from './solver/types';
import { seed, withSourceLimit } from './adapters/sourceContour';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';
import { selectionConstraints } from './tools/constraints';
import { sketchViewKey } from './viewPreferences';

let host: HTMLDivElement, root: Root, current: Sketch, session: CanvasSession;
const context: SketchContext = { fingerprint: 'wall-1', rimPlaneLabel: '', items: [
  { id: 'wall', kind: 'segment', geometry: [{ x: 30, y: 0 }, { x: 30, y: 10 }], role: 'wall', label: 'Wall', pickable: true },
  { id: 'corner', kind: 'point', geometry: [{ x: 30, y: 0 }], owner: 'wall', role: 'wall', label: 'Corner', pickable: true },
] };
const sequences: [SelectionConstraint, string[]][] = [
  ['coincident', ['a', 'c']], ['horizontal', ['line']], ['vertical', ['line']], ['parallel', ['line', 'other']],
  ['perpendicular', ['line', 'other']], ['tangent', ['line', 'arc']], ['equal', ['line', 'other']],
  ['midpoint', ['a', 'other']], ['fix', ['a']], ['pointOnLine', ['a', 'other']], ['pointOnArc', ['a', 'arc']],
];
function render() { root.render(<SketchCanvas sketch={current} session={session} context={context} references={[]} problems={[]}/>); }
beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.removeItem(sketchViewKey);
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  current = importedSketch([
    { id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }, { id: 'c', x: 4, y: 8 }, { id: 'd', x: 14, y: 12 },
    { id: 'center', x: 20, y: 20 }, { id: 'start', x: 25, y: 20 }, { id: 'end', x: 20, y: 25 },
  ], [{ id: 'line', kind: 'line', p1: 'a', p2: 'b' }, { id: 'other', kind: 'line', p1: 'c', p2: 'd' },
    { id: 'arc', kind: 'arc', center: 'center', start: 'start', end: 'end', ccw: true }]);
  const commit = (result: ReturnType<typeof applyCommand>) => { if ('error' in result) throw new Error(result.error); current = result.sketch; render(); return current; };
  session = {
    capabilities: capabilities(),
    command: vi.fn(async command => { for (const c of Array.isArray(command) ? command : [command]) commit(applyCommand(current, c)); return current; }),
    constraint: vi.fn(async constraint => commit(applyCommand(current, { kind: 'addConstraint', constraint }))),
    materialiseContext: vi.fn(async (context, id) => { const result = materialiseContext(current, context, id); current = result.sketch; render(); return result; }),
    dimension: vi.fn(async () => current), undo: vi.fn(async () => {}), beginDrag: vi.fn(async () => {}),
    dragTo: vi.fn(), endDrag: vi.fn(async () => {}),
  };
  await act(async () => render());
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
const svg = () => host.querySelector<SVGSVGElement>('[role="application"]')!;
const button = (label: string) => host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
const key = async (key: string) => act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })); });
const click = async (label: string) => act(async () => button(label).click());
function screen(at: XY) {
  const a = host.querySelector('[data-id="a"].sketch-point')!, b = host.querySelector('[data-id="b"].sketch-point')!;
  const scale = (Number(b.getAttribute('cx')) - Number(a.getAttribute('cx'))) / 10;
  return { x: Number(a.getAttribute('cx')) + at.x * scale, y: Number(a.getAttribute('cy')) - at.y * scale };
}
async function pointer(at: XY, type = 'pointerdown', target: Element = svg()) {
  const p = screen(at);
  await act(async () => { target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: p.x, clientY: p.y, buttons: 1 })); });
}
async function pick(id: string, type = 'pointerdown') {
  const point = current.points.find(p => p.id === id), e = current.entities.find(e => e.id === id);
  let at = point;
  if (e?.kind === 'line') {
    const a = current.points.find(p => p.id === e.p1)!, b = current.points.find(p => p.id === e.p2)!;
    at = { id, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  } else if (e?.kind === 'arc') at = { id, x: 20 + 5 / Math.sqrt(2), y: 20 + 5 / Math.sqrt(2) };
  await pointer(at!, type, host.querySelector(`[data-id="${id}"]`) ?? svg());
}

describe('constraint palette wiring', () => {
  it.each(sequences)('applies %s tool-first through the session, ignores empty picks, and repeats', async (kind, ids) => {
    await click(rules[kind].label);
    const hint = host.querySelector('.sketch-status')!.textContent!.split(' · ').at(-1);
    await pointer({ x: 50, y: 30 });
    expect(session.constraint).not.toHaveBeenCalled(); expect(host.querySelector('.sketch-status')!.textContent).toContain(hint);
    const expected = constraintFor(current, ids, kind);
    for (const id of ids) await pick(id);
    expect(session.constraint).toHaveBeenLastCalledWith(expected);
    expect(button(rules[kind].label).getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelector('[aria-label="Selection actions"]')).toBeNull();
    for (const id of ids) await pick(id);
    expect(session.constraint).toHaveBeenCalledTimes(2);
  });
  it('keeps only valid targets hovered, cancels picks then exits on Escape, and starts from a key', async () => {
    await key('p'); expect(button('Parallel').getAttribute('aria-pressed')).toBe('true');
    await pick('arc', 'pointermove'); expect(host.querySelector('.sketch-entity.hovered')).toBeNull();
    await pick('line', 'pointermove'); expect(host.querySelector('.sketch-entity.hovered')?.getAttribute('data-id')).toBe('line');
    await pick('line'); expect(host.textContent).toContain('Parallel: select the second line');
    await key('Escape'); expect(host.textContent).toContain('Parallel: select the first line');
    expect(button('Parallel').getAttribute('aria-pressed')).toBe('true');
    await key('Escape'); expect(button('Select (Esc)').getAttribute('aria-pressed')).toBe('true');
  });
  it.each(['parallel', 'perpendicular', 'pointOnLine', 'coincident'] as const)('materialises a valid context target for %s', async kind => {
    await click(rules[kind].label);
    await pick(kind === 'pointOnLine' || kind === 'coincident' ? 'a' : 'line');
    const id = kind === 'coincident' ? 'corner' : 'wall';
    await pointer({ x: 30, y: kind === 'coincident' ? 0 : 5 }, 'pointerdown', host.querySelector(`[data-context-id="${id}"] .sketch-context-hit`)!);
    expect(session.materialiseContext).toHaveBeenCalledTimes(1);
    expect(session.constraint).toHaveBeenCalledWith(expect.objectContaining({ kind }));
    expect(button(rules[kind].label).getAttribute('aria-pressed')).toBe('true');
  });
  it.each(['equal', 'midpoint'] as const)('never materialises invalid %s context picks', async kind => {
    await click(rules[kind].label); await pick(kind === 'equal' ? 'line' : 'a');
    const target = host.querySelector<SVGPathElement>('[data-context-id="wall"] .sketch-context-hit')!;
    expect(target.style.pointerEvents).toBe('none');
    await pointer({ x: 30, y: 5 }, 'pointerdown', target);
    expect(session.materialiseContext).not.toHaveBeenCalled(); expect(session.constraint).not.toHaveBeenCalled();
  });
  it('applies a valid selection from the palette and disables incompatible constraints', async () => {
    await pick('line');
    expect(button('Horizontal').disabled).toBe(false); expect(button('Coincident').disabled).toBe(true);
    const popup = host.querySelector('[aria-label="Selection actions"]')!;
    expect(popup.textContent).toContain('Construction'); expect(popup.textContent).not.toContain('Horizontal');
    await click('Horizontal'); expect(session.constraint).toHaveBeenCalledWith({ kind: 'horizontal', line: 'line' });
  });
  it('offers no constraints for the throat lock alone or exclusively fixed points', async () => {
    const state = withSourceLimit(seed(INITIAL_SOURCE), { kind: 'throat', radius: 8, hard: true });
    const lock = state.meta.references.find(r => r.kind === 'limit')!;
    for (const selection of [[lock.id], state.sketch.entities.filter(e => e.reference).flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : [])]) {
      await act(async () => root.render(<SketchCanvas sketch={state.sketch} session={session} references={state.meta.references} problems={[]} selection={selection}/>));
      expect(selectionConstraints(state.sketch, selection, session.capabilities)).toEqual([]);
      for (const label of ['Horizontal', 'Vertical', 'Fix', 'Coincident']) expect(button(label).disabled).toBe(true);
    }
    current.constraints.push({ kind: 'fix', id: 'held-a', point: 'a' });
    await act(async () => root.render(<SketchCanvas sketch={current} session={session} references={[]} problems={[]} selection={['a']}/>));
    expect(button('Fix').disabled).toBe(true); expect(button('Coincident').disabled).toBe(true);
  });
  it.each(sequences)('reuses complete or partial preselection for %s', async (kind, ids) => {
    await pick(ids[0]); await pointer({ x: 50, y: 30 }, 'pointerup');
    expect(button(rules[kind].label).disabled).toBe(false);
    const expected = constraintFor(current, ids, kind);
    await click(rules[kind].label);
    if (ids.length > 1) {
      expect(session.constraint).not.toHaveBeenCalled();
      expect(host.querySelector(`[data-id="${ids[0]}"].selected`)).not.toBeNull();
      await pick(ids[1]);
    }
    expect(session.constraint).toHaveBeenLastCalledWith(expected);
  });
  it('explains an incompatible command without losing the selection', async () => {
    await pick('arc'); await key('p');
    expect(host.textContent).toContain('Parallel: select line and line');
    expect(host.querySelector('.sketch-entity[data-id="arc"].selected')).not.toBeNull();
    expect(session.constraint).not.toHaveBeenCalled();
  });
  it('uses the existing conflict banner on a rejected palette constraint', async () => {
    vi.mocked(session.constraint).mockRejectedValueOnce({ overconstrained: true, constraints: [{ kind: 'horizontal', line: 'line', id: 'h' }], reason: 'Conflicts with Horizontal' });
    await click('Horizontal'); await pick('line');
    expect(host.querySelector('.sketch-conflict-banner')).not.toBeNull();
    expect(button('Horizontal').getAttribute('aria-pressed')).toBe('true');
  });
  it('cancels a context pick still being materialised when Escape is pressed', async () => {
    const original = vi.mocked(session.materialiseContext!).getMockImplementation()!;
    let release!: () => void;
    vi.mocked(session.materialiseContext!).mockImplementation(async (...args) => {
      await new Promise<void>(resolve => { release = resolve; });
      return original(...args);
    });
    await click('Parallel'); await pick('line');
    await pointer({ x: 30, y: 5 }, 'pointerdown', host.querySelector('[data-context-id="wall"] .sketch-context-hit')!);
    await key('Escape');
    await act(async () => release());
    expect(session.constraint).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Parallel: select the first line');
    expect(button('Parallel').getAttribute('aria-pressed')).toBe('true');
  });
  it('offers an arc centre to a point constraint while the tool is active', async () => {
    await click('Fix');
    expect(host.querySelector('[data-id="center"]')).not.toBeNull();
    await pick('center'); expect(session.constraint).toHaveBeenCalledWith({ kind: 'fix', point: 'center' });
  });
  it('hides unsupported rules and offers only the supported Point on variant', async () => {
    session.capabilities!.constraints.parallel.support = 'unsupported'; session.capabilities!.constraints.pointOnLine.support = 'unsupported';
    await act(async () => render());
    expect(button('Parallel')).toBeNull();
    await click('Point on'); await pick('a'); await pick('other'); expect(session.constraint).not.toHaveBeenCalled();
    await pick('arc'); expect(session.constraint).toHaveBeenCalledWith({ kind: 'pointOnArc', point: 'a', arc: 'arc' });
  });
});
