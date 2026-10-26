import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Workspace } from './Workspace';
import type { DomainAdapter, EditStatus, WorkspaceSession } from './types';
import type { XY } from '../solver/types';
import { sketchViewKey } from '../viewPreferences';

type Point = { id: string; at: XY };
type State = { points: Point[] };
type Command = { kind: 'move'; id: string; at: XY } | { kind: 'delete'; ids: string[] };
const initial = (): State => ({ points: [{ id: 'a', at: { x: 0, y: 0 } }, { id: 'b', at: { x: 10, y: 10 } }] });
const adapter: DomainAdapter<State, State, Command> = {
  id: 'two-points', axes: { x: { label: 'z', unit: 'mm' }, y: { label: 'r', unit: 'mm' } },
  seed: document => document, lower: state => ({ ok: true, document: state }), serializeAuthoring: () => null,
  status: () => ({ kind: 'valid' }),
  apply: (state, command) => ({ points: command.kind === 'delete' ? state.points.filter(p => !command.ids.includes(p.id)) : state.points.map(p => p.id === command.id ? { ...p, at: command.at } : p) }),
  remove: (_state, ids) => [{ kind: 'delete', ids }],
  display: (state, ui) => ({ context: [], bounds: () => ({ minX: 0, maxX: 10, minY: 0, maxY: 10 }), items: state.points.map(p => ({ ...p, kind: 'point', role: 'anchor', editable: true, state: ui.selection.includes(p.id) ? ['selected'] : [] })) }),
  hits: (state, at) => state.points.map(p => ({ id: p.id, kind: 'point', gap: Math.hypot(p.at.x - at.x, p.at.y - at.y) })),
  marquee: (state, box) => state.points.filter(p => p.at.x >= box.minX && p.at.x <= box.maxX && p.at.y >= box.minY && p.at.y <= box.maxY).map(p => p.id),
  dragSet: (state, selection, hit) => {
    const ids = selection.includes(hit) ? selection : [hit];
    return { ids, move: (_current, delta) => state.points.filter(p => ids.includes(p.id)).map(p => ({ kind: 'move', id: p.id, at: { x: p.at.x + delta.x, y: p.at.y + delta.y } })) };
  },
  tools: [], properties: (_state, selection) => selection.length ? [{ id: 'selected', title: 'Selected points', render: () => selection.join(', ') }] : [],
};

/** Test-only session: W3 owns the production PlainController. No Sketch or WASM. */
class ToySession implements WorkspaceSession<State, Command> {
  current = initial(); status: EditStatus = { kind: 'valid' }; busy = false;
  rejection = new Error('A solver-only rejection must stay hidden');
  past: State[] = []; future: State[] = []; start?: State; ids: string[] = []; events: string[] = [];
  constructor(private changed: (state: State) => void) {}
  install(state: State) { this.current = state; this.changed(state); }
  async command(commands: Command | Command[] | ((state: State) => Command | Command[])) {
    const value = typeof commands === 'function' ? commands(this.current) : commands;
    let state = this.current;
    for (const command of Array.isArray(value) ? value : [value]) { const result = adapter.apply(state, command); if ('error' in result) throw Error(result.error); state = result; }
    this.past.push(this.current); this.future = []; this.install(state); return state;
  }
  async beginDrag(ids: string[]) { this.events.push('begin'); this.start = this.current; this.ids = ids; }
  async dragTo(targets: { id: string; at: XY }[]) {
    this.events.push('frame');
    let state = this.current;
    for (const target of targets) {
      const before = this.start!.points.find(p => p.id === target.id)!;
      const move = adapter.dragSet(this.start!, [target.id], target.id)!;
      for (const command of move.move(state, { x: target.at.x - before.at.x, y: target.at.y - before.at.y }, target.at)) {
        const result = adapter.apply(state, command); if ('error' in result) throw Error(result.error); state = result;
      }
    }
    this.install(state); return { stopped: false };
  }
  async endDrag(cancel = false) {
    this.events.push(cancel ? 'cancel' : 'end');
    if (cancel) this.install(this.start!);
    else if (JSON.stringify(this.current) !== JSON.stringify(this.start)) { this.past.push(this.start!); this.future = []; }
    this.start = undefined;
  }
  async undo(redo = false) {
    const from = redo ? this.future : this.past, to = redo ? this.past : this.future;
    const state = from.pop(); if (state) { to.push(this.current); this.install(state); }
  }
}
let host: HTMLDivElement, root: Root, session: ToySession;
let frames: Map<number, FrameRequestCallback>, frameId: number;
function Harness({ domain = adapter }: { domain?: typeof adapter }) {
  const [state, setState] = useState(initial);
  const [controller] = useState(() => new ToySession(setState)); session = controller;
  return <Workspace adapter={domain} state={state} session={controller}/>;
}
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.removeItem(sketchViewKey); frames = new Map(); frameId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const svg = () => host.querySelector<SVGSVGElement>('svg[role="application"]')!;
const point = (id: string) => host.querySelector<SVGCircleElement>(`circle[role="button"][data-id="${id}"]`)!;
const xy = (id: string) => ({ x: Number(point(id).getAttribute('cx')), y: Number(point(id).getAttribute('cy')) });
const pointer = async (element: Element, type: string, at: XY, options = {}) => act(async () => { element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: at.x, clientY: at.y, buttons: type === 'pointerup' ? 0 : 1, ...options })); });
const key = async (value: string, options = {}, target: Element = svg()) => act(async () => { target.dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true, ...options })); });
async function mount(domain = adapter) {
  await act(async () => root.render(<Harness domain={domain}/>));
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
}
async function marquee() {
  const a = xy('a'), b = xy('b');
  await pointer(svg(), 'pointerdown', { x: Math.min(a.x, b.x) - 20, y: Math.min(a.y, b.y) - 20 });
  await pointer(svg(), 'pointermove', { x: Math.max(a.x, b.x) + 20, y: Math.max(a.y, b.y) + 20 });
  expect(host.querySelector('.sketch-marquee')).not.toBeNull();
  await pointer(svg(), 'pointerup', { x: Math.max(a.x, b.x) + 20, y: Math.max(a.y, b.y) + 20 });
}

describe('Workspace without a solver', () => {
  it('uses adapter axes, properties and tool lists, with no solver UI or analysis classes', async () => {
    const toolPointer = vi.fn(() => ({}));
    await mount({ ...adapter, tools: [
      { id: 'domain-tool', group: 'Anchors', label: 'Add anchor', icon: 'M0 0L1 1', shortcut: 'a', hint: () => 'Choose a position', pointer: toolPointer },
      { id: 'constraint', group: 'Constraints', label: 'Impossible constraint', icon: '', hint: () => '', pointer: toolPointer },
    ] });
    expect(host.querySelector('[aria-label="Add anchor"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Impossible constraint"]')).toBeNull();
    expect(host.querySelector('[aria-label="Constraints"]')).toBeNull();
    expect(host.querySelector('.sketch-conflict-banner, .sketch-dof, .determined, .under, .not-analysed')).toBeNull();
    expect(host.querySelector('.sketch-status')?.textContent).toContain('z 0.00, r 0.00 mm');
    expect(host.textContent).not.toContain('Solve:');
    await key('a'); await pointer(svg(), 'pointerdown', { x: 200, y: 100 });
    expect(toolPointer).toHaveBeenCalledWith(expect.objectContaining({ phase: 'down' }), expect.anything(), undefined);
    await key('Escape');
    const at = xy('a'); await pointer(point('a'), 'pointerdown', at); await pointer(svg(), 'pointerup', at);
    expect(host.querySelector('[aria-label="Selected points"]')?.textContent).toBe('a');
  });

  it('lets domain sections own Escape and canvas shortcuts, including through a properties portal', async () => {
    const inputKey = vi.fn(), treeKey = vi.fn();
    const domain: typeof adapter = { ...adapter, tools: ['l', 'a', 'd', 's'].map(shortcut => ({
      id: shortcut, shortcut, group: 'Domain', label: shortcut, icon: '', hint: () => shortcut, pointer: () => ({}),
    })), properties: () => [{ id: 'wiring', title: 'Wiring', render: () => <>
      <input aria-label="Component value" onKeyDown={inputKey}/><div role="treeitem" tabIndex={0} onKeyDown={treeKey}>Series branch</div>
    </> }] };
    const portal = document.createElement('div');
    const changed = vi.fn(); session = new ToySession(() => {});
    await act(async () => root.render(<Workspace adapter={domain} state={session.current} session={session}
      selection={['a']} onSelectionChange={changed} slots={{ properties: portal }}/>));
    host.append(portal);
    expect(portal.querySelector('[aria-label="Wiring"][data-workspace-own-keys]')).not.toBeNull();
    const input = portal.querySelector('input')!, tree = portal.querySelector<HTMLElement>('[role="treeitem"]')!;
    input.focus(); await key('Escape', {}, input);
    expect(inputKey).toHaveBeenCalledOnce(); expect(inputKey.mock.calls[0][0].defaultPrevented).toBe(false);
    expect(changed).not.toHaveBeenCalled(); expect(point('a').classList.contains('selected')).toBe(true);
    // Zoom first so an accidental F shortcut would visibly change the grid.
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Zoom in"]')!.click());
    const grid = host.querySelector('.sketch-grid')!.innerHTML;
    tree.focus();
    for (const value of ['Delete', 'Backspace', 'l', 'a', 'd', 's', 'f']) await key(value, {}, tree);
    expect(treeKey).toHaveBeenCalledTimes(7);
    expect(treeKey.mock.calls.every(([event]) => !event.defaultPrevented)).toBe(true);
    expect(session.current).toEqual(initial()); expect(session.past).toHaveLength(0);
    expect(changed).not.toHaveBeenCalled();
    expect(host.querySelector('[aria-label="Select (Esc)"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelector('.sketch-grid')!.innerHTML).toBe(grid);
    // Ordinary canvas Escape still owns the selection.
    await key('Escape'); expect(changed).toHaveBeenCalledWith([]);
  });

  it('shows domain properties without a canvas selection and uses adapter aria labels', async () => {
    await mount({ ...adapter, labels: { drawing: 'Assembly drawing', tools: 'Assembly tools' },
      properties: () => [{ id: 'tree', title: 'Wiring', render: () => <div role="tree">Components</div> }] });
    expect(svg().getAttribute('aria-label')).toBe('Assembly drawing');
    expect(host.querySelector('[role="toolbar"][aria-label="Assembly tools"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Wiring"]')?.textContent).toBe('Components');
  });

  it('skips drag ids without display positions and still drags the visible points', async () => {
    await mount({ ...adapter, dragSet: (state, selection, hit) => {
      const set = adapter.dragSet(state, selection, hit)!;
      return { ...set, ids: [...set.ids, 'hidden'] };
    } });
    const at = xy('a'); await pointer(point('a'), 'pointerdown', at);
    await pointer(svg(), 'pointermove', { x: at.x + 30, y: at.y });
    await pointer(svg(), 'pointerup', { x: at.x + 30, y: at.y });
    expect(session.ids).toEqual(['a']); expect(session.events).toEqual(['begin', 'frame', 'end']);
    expect(session.current.points[0].at.x).toBeGreaterThan(0);
    expect(session.past).toHaveLength(1);
  });

  it('keeps adapter tool Escape stages and never sends a tool release for a Space pan', async () => {
    const toolPointer = vi.fn(() => ({}));
    const toolKey = vi.fn((_event: KeyboardEvent, _state: State, local: unknown) => ({ consumed: local === undefined, local: 'cancelled' }));
    await mount({ ...adapter, tools: [{ id: 'staged', group: 'Anchors', label: 'Staged tool', shortcut: 'a', icon: '', hint: () => 'Active stage', pointer: toolPointer, key: toolKey }] });
    await key('a'); await key(' ');
    await pointer(svg(), 'pointerdown', { x: 200, y: 100 });
    await pointer(svg(), 'pointermove', { x: 220, y: 110 });
    await pointer(svg(), 'pointerup', { x: 220, y: 110 });
    expect(toolPointer).not.toHaveBeenCalled();
    await key('Escape');
    expect(host.querySelector('[aria-label="Staged tool"]')?.getAttribute('aria-pressed')).toBe('true');
    await key('Escape');
    expect(host.querySelector('[aria-label="Select (Esc)"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(toolKey).toHaveBeenCalledTimes(2);
  });

  it('coalesces two draggable points, flushes before release, and records one undo step', async () => {
    await mount();
    const start = structuredClone(session.current), at = xy('a');
    const capture = vi.fn().mockImplementationOnce(() => { expect(document.activeElement).not.toBe(svg()); });
    Object.defineProperty(svg(), 'setPointerCapture', { value: capture, configurable: true });
    await pointer(point('a'), 'pointerdown', at);
    expect(capture).toHaveBeenCalledOnce(); expect(document.activeElement).toBe(svg());
    await pointer(svg(), 'pointermove', { x: at.x + 20, y: at.y - 10 });
    await pointer(svg(), 'pointermove', { x: at.x + 40, y: at.y - 20 });
    expect(session.events).toEqual(['begin']); expect(session.past).toHaveLength(0);
    await pointer(svg(), 'pointerup', { x: at.x + 40, y: at.y - 20 });
    expect(session.events).toEqual(['begin', 'frame', 'end']); expect(session.past).toHaveLength(1);
    expect(session.current.points[0].at.x).toBeGreaterThan(start.points[0].at.x);
    expect(session.current.points[0].at.y).toBeGreaterThan(start.points[0].at.y);
    expect(session.current.points[1]).toEqual(start.points[1]);
    expect(frames.size).toBe(0);
    await key('z', { ctrlKey: true }); expect(session.current).toEqual(start);
    const b = xy('b'); await pointer(point('b'), 'pointerdown', b); await pointer(svg(), 'pointermove', { x: b.x - 30, y: b.y + 15 }); await pointer(svg(), 'pointerup', b);
    expect(session.current.points[1].at.x).toBeLessThan(10);
  });

  it('marquees and drags a group, then Delete and Undo/Redo use the session history', async () => {
    await mount(); await marquee();
    expect(point('a').classList.contains('selected')).toBe(true); expect(point('b').classList.contains('selected')).toBe(true);
    const a = xy('a'); await pointer(point('a'), 'pointerdown', a); await pointer(svg(), 'pointermove', { x: a.x + 30, y: a.y + 20 }); await pointer(svg(), 'pointerup', a);
    expect(session.ids).toEqual(['a', 'b']); expect(session.past).toHaveLength(1);
    const delta = session.current.points[0].at;
    expect(session.current.points[1].at).toEqual({ x: 10 + delta.x, y: 10 + delta.y });
    const moved = structuredClone(session.current);
    await key('Delete'); expect(session.current.points).toEqual([]); expect(session.past).toHaveLength(2);
    await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Undo')!.click());
    expect(session.current).toEqual(moved);
    await key('z', { metaKey: true, shiftKey: true }); expect(session.current.points).toEqual([]);
  });

  it('document-capture Escape cancels an active drag before the modal, without a history step', async () => {
    await mount(); const start = structuredClone(session.current), at = xy('a'), modal = vi.fn();
    host.addEventListener('keydown', modal);
    await pointer(point('a'), 'pointerdown', at); await pointer(svg(), 'pointermove', { x: at.x + 40, y: at.y });
    await act(async () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(0)); });
    expect(session.current).not.toEqual(start);
    await key('Escape', {}, document.body);
    expect(session.current).toEqual(start); expect(session.past).toHaveLength(0); expect(session.events.at(-1)).toBe('cancel');
    expect(modal).not.toHaveBeenCalled(); expect(frames.size).toBe(0);
    await key('Escape'); expect(modal).toHaveBeenCalledOnce();
  });

  it('cancels captured gestures on pointer cancellation and preserves controlled selection', async () => {
    const selection = vi.fn();
    session = new ToySession(() => {});
    await act(async () => root.render(<Workspace adapter={adapter} state={session.current} session={session} selection={['b']} onSelectionChange={selection}/>));
    const at = xy('a');
    await pointer(point('a'), 'pointerdown', at); expect(selection).toHaveBeenCalledWith(['a']);
    expect(point('b').classList.contains('selected')).toBe(true);
    await pointer(svg(), 'pointermove', { x: at.x + 40, y: at.y }); await pointer(svg(), 'pointercancel', at);
    expect(session.events).toEqual(['begin', 'cancel']); expect(session.past).toHaveLength(0); expect(frames.size).toBe(0);
  });
});
