import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { designForFamily, resetDesignStore, useDesignStore } from '../../stores/design';
import { editRecord } from './editRecord';
import { PlainController } from './plainController';
import { ProjectHistoryAuthority } from './projectHistoryAuthority';
import { Workspace } from './Workspace';
import { adapter, initial, profileBinding, type Command, type State } from './profileTestSupport';

let host: HTMLDivElement, root: Root, controller: PlainController<unknown, State, Command>;
let frames: Map<number, FrameRequestCallback>, frameId: number;
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  frames = new Map(); frameId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  resetDesignStore(); useDesignStore.getState().replaceDocument(designForFamily('FREEFORM'));
  useDesignStore.getState().commitEmbedded({ profile_h: editRecord(adapter, initial, null) }, 'amend');
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); controller.destroy(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); resetDesignStore(); });
const svg = () => host.querySelector<SVGSVGElement>('svg[role="application"]')!;
const point = (id: string) => host.querySelector<SVGCircleElement>(`circle[role="button"][data-id="${id}"]`)!;
const xy = (id: string) => ({ x: Number(point(id).getAttribute('cx')), y: Number(point(id).getAttribute('cy')) });
const key = (value: string, options = {}) => act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true, ...options })); });
const pointer = (element: Element, type: string, at: { x: number; y: number }) => act(async () => {
  element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: at.x, clientY: at.y, buttons: type === 'pointerup' ? 0 : 1 }));
});
async function mount() {
  const authority = new ProjectHistoryAuthority(profileBinding(() => {}));
  const toolPointer = vi.fn(() => ({ local: 'pending' }));
  const domain: typeof adapter = { ...adapter, tools: [{ id: 'insert', group: 'Anchors', label: 'Insert', shortcut: 'a', icon: '', hint: () => 'Pick anchor', pointer: toolPointer }] };
  let update: (state: State) => void = () => {};
  controller = new PlainController({ initial, adapter: domain, authority, changed: state => update(state), preview: state => update(state) });
  function Harness() {
    const [state, setState] = useState(controller.current); update = setState;
    return <Workspace adapter={domain} state={state} session={controller}/>;
  }
  await act(async () => root.render(<Harness/>));
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
  return toolPointer;
}
async function command(r: number) { await act(async () => { await controller.command({ r }); }); }
async function storeAction(action: () => void) { await act(async () => { action(); await controller.enqueue(async () => {}); }); }

it('routes canvas Ctrl/Cmd+Z and toolbar history through the project store', async () => {
  await mount(); await command(160);
  await key('z', { ctrlKey: true }); expect(controller.current).toEqual(initial);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
  await key('z', { metaKey: true, shiftKey: true }); expect(controller.current.points[1].r).toBe(160);
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Undo')!.click());
  expect(controller.current).toEqual(initial);
  await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Redo')!.click());
  expect(controller.current.points[1].r).toBe(160);
});
it('first Ctrl+Z cancels a live gesture; the second undoes the preceding project step', async () => {
  await mount(); await command(160);
  const at = xy('1'); await pointer(point('1'), 'pointerdown', at);
  await pointer(svg(), 'pointermove', { x: at.x + 20, y: at.y - 20 });
  await act(async () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); });
  expect(controller.current.points[1].r).toBeGreaterThan(160);
  await key('z', { ctrlKey: true });
  expect(controller.current.points[1].r).toBe(160); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  expect(useDesignStore.getState().dragSnapshot).toBeNull(); expect(frames.size).toBe(0);
  await key('z', { ctrlKey: true }); expect(controller.current).toEqual(initial);
  await command(170); await key('a');
  await pointer(svg(), 'pointerdown', { x: 200, y: 100 });
  await key('z', { metaKey: true }); expect(controller.current.points[1].r).toBe(170);
  await key('z', { metaKey: true }); expect(controller.current).toEqual(initial);
});
it('TopBar Undo cancels a captured canvas drag and cannot publish a later pointer release', async () => {
  await mount(); const at = xy('1'); await pointer(point('1'), 'pointerdown', at);
  await pointer(svg(), 'pointermove', { x: at.x + 20, y: at.y - 30 });
  await storeAction(() => useDesignStore.getState().undo());
  await pointer(svg(), 'pointerup', at);
  expect(controller.current).toEqual(initial); expect(frames.size).toBe(0);
  expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
});
it('keeps zoom and surviving selections on external edits, and refits with no selection on Open', async () => {
  await mount(); const at = xy('1'); await pointer(point('1'), 'pointerdown', at); await pointer(svg(), 'pointerup', at);
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Zoom in"]')!.click());
  const grid = host.querySelector('.sketch-grid')!.innerHTML;
  await command(160); await storeAction(() => useDesignStore.getState().undo());
  expect(point('1').classList.contains('selected')).toBe(true); expect(host.querySelector('.sketch-grid')!.innerHTML).toBe(grid);
  await storeAction(() => useDesignStore.getState().commitEmbedded({ profile_h: editRecord(adapter, { ...initial, points: initial.points.slice(0, 1) }, null) }, 'step'));
  expect(host.querySelector('.selected')).toBeNull();
  await storeAction(() => useDesignStore.getState().replaceDocument({ ...useDesignStore.getState().design, profile_h: editRecord(adapter, initial, null) } as unknown as ReturnType<typeof designForFamily>));
  expect(point('1').classList.contains('selected')).toBe(false);
  expect(host.querySelector('.sketch-grid')!.innerHTML).not.toBe(grid);
});

it('drops a queued drag start after project Undo without showing a stale-work error', async () => {
  await mount(); await command(160);
  let release!: () => void;
  const blocker = controller.enqueue(() => new Promise<void>(resolve => { release = resolve; }));
  await act(async () => { await vi.waitFor(() => expect(release).toBeTypeOf('function')); });
  const at = xy('1'); await pointer(point('1'), 'pointerdown', at);
  await pointer(svg(), 'pointermove', { x: at.x + 20, y: at.y - 20 });
  await act(async () => {
    useDesignStore.getState().undo(); release(); await blocker; await controller.enqueue(async () => {});
  });
  expect(controller.current).toEqual(initial);
  expect(host.querySelector('.sketch-status')!.textContent).not.toContain('project changed');
  expect(useDesignStore.getState().dragSnapshot).toBeNull();
});

it('updates toolbar availability from project history, including unrelated edits, drag cancellation and loads', async () => {
  await mount();
  const button = (label: string) => [...host.querySelectorAll('button')].find(button => button.textContent === label)!;
  const availability = () => [button('Undo').disabled, button('Redo').disabled];
  expect(availability()).toEqual([true, true]);
  await storeAction(() => useDesignStore.getState().updateValue('length', 123));
  expect(availability()).toEqual([false, true]);
  await storeAction(() => useDesignStore.getState().undo());
  expect(availability()).toEqual([true, false]);
  await storeAction(() => useDesignStore.getState().redo());
  expect(availability()).toEqual([false, true]);
  await storeAction(() => useDesignStore.getState().replaceDocument(structuredClone(useDesignStore.getState().design)));
  expect(availability()).toEqual([true, true]);
  // An active drag counts as Undo even before a frame adds a temporal entry.
  await act(async () => { await controller.beginDrag(['1']); });
  expect(availability()).toEqual([false, true]);
  await act(async () => { await controller.endDrag(true); });
  expect(availability()).toEqual([true, true]);
});
it('preserves the active canvas tool and its input across an unrelated parameter edit', async () => {
  const toolPointer = await mount(); await key('a');
  await pointer(svg(), 'pointerdown', { x: 200, y: 100 });
  await storeAction(() => useDesignStore.getState().updateValue('length', 123));
  expect(host.querySelector('[aria-label="Insert"]')?.getAttribute('aria-pressed')).toBe('true');
  await pointer(svg(), 'pointerdown', { x: 220, y: 100 });
  expect(toolPointer).toHaveBeenLastCalledWith(expect.anything(), initial, 'pending');
  expect(controller.current).toEqual(initial);
});
