import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addWoofer, emptyAssembly, joinWiringDrivers, setMemberProcessing, setWiringGroup, setWooferDrive,
  type AssemblyWiringNode, type DesignAssembly,
} from '../stores/assembly';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { frontViewAdapter, frontViewBinding, type FrontCommand } from '../sketch/adapters/frontView';
import { PlainController } from '../sketch/workspace/plainController';
import { ProjectHistoryAuthority } from '../sketch/workspace/projectHistoryAuthority';
import { frontViewProperties } from './FrontProperties';

let counter: number, first: string, second: string, third: string;
const ids = () => `00000000-0000-4000-8000-${String(++counter).padStart(12, '0')}`;
let host: HTMLDivElement, root: Root;
let controller: PlainController<DesignAssembly, DesignAssembly, FrontCommand>;
let selection: string[];
const assembly = () => useDesignStore.getState().design.assembly!;
const network = () => assembly().wiring_groups![0].network;
const steps = () => useDesignStore.temporal.getState().pastStates.length;
const node = (path = '') => host.querySelector<HTMLElement>(`[data-wiring-path="${path}"]`)!;
const ownButton = (element: Element, label: string) => [...element.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === label)!;
async function click(element: HTMLElement) { expect(element).toBeTruthy(); await act(async () => { element.click(); }); }
async function choose(label: string, target: Element = host) {
  await click(ownButton(target, target === host ? 'Wire woofers…' : 'Edit…'));
  await click([...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')].find(item => item.textContent === label)!);
}
async function select(element: HTMLSelectElement, value: string) {
  await act(async () => { element.value = value; element.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function enter(element: HTMLInputElement, value: string) {
  await act(async () => { element.focus(); });
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => { element.blur(); });
}
async function oneUndo(action: () => Promise<void>) {
  const before = structuredClone(assembly()), count = steps();
  await action();
  expect(steps()).toBe(count + 1);
  const after = structuredClone(assembly());
  await act(async () => { await controller.undo(); });
  expect(assembly()).toEqual(before);
  await act(async () => { await controller.undo(true); });
  expect(assembly()).toEqual(after);
}
async function mount() {
  const properties = frontViewProperties(() => undefined, () => true);
  const adapter = frontViewAdapter(() => undefined, properties);
  const authority = new ProjectHistoryAuthority(frontViewBinding(emptyAssembly(ids)));
  controller = new PlainController({ initial: authority.current, adapter, authority, changed: () => {} });
  function Harness() {
    const state = useDesignStore(store => store.design.assembly!);
    const wiring = properties(state, selection, controller, { setSelection: vi.fn() }).find(section => section.id === 'wiring');
    return wiring ? <section aria-label="Wiring">{wiring.render()}</section> : null;
  }
  await act(async () => { root.render(<Harness/>); });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  counter = 0; resetDesignStore(); resetSolveOptionsStore(); workspaceModeStore.setMode('parametric');
  useSolveOptionsStore.getState().setEngine('beat-cpu');
  let state = emptyAssembly(ids);
  const a = addWoofer(state, { anchor: 'below' }, ids); first = a.id;
  const b = addWoofer(a.assembly, { anchor: 'above' }, ids); second = b.id;
  const c = addWoofer(b.assembly, { anchor: 'free' }, ids); third = c.id;
  state = [first, second, third].reduce((current, id) => setWooferDrive(current, id, 'driver'), c.assembly);
  useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: state });
  selection = [first, second];
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount()); controller?.destroy(); host.remove();
  vi.unstubAllGlobals(); vi.restoreAllMocks(); resetDesignStore();
});

describe('Sources and baffle wiring', () => {
  it.each(['Series', 'Parallel'])('creates %s from selected drivers, joining their signal group in one Undo', async kind => {
    await mount();
    await oneUndo(() => choose(kind));
    expect(network()).toEqual({ kind: kind.toLowerCase(), children: [
      { kind: 'driver', radiator_id: first }, { kind: 'driver', radiator_id: second },
    ] });
    expect(assembly().signal_groups!.find(group => group.members.some(member => member.radiator_id === first))!.members)
      .toEqual([{ radiator_id: first }, { radiator_id: second }]);
    expect(host.textContent).toContain('individual, each on its own amplifier channel');
    expect(host.textContent).toContain('Woofer 1');
  });

  it.each([['R', '2.5', 2.5, '1 Ω'], ['L', '3.3', 0.0033, '1 mH'], ['C', '47', 0.000047, '10 µF']] as const)
    ('adds and edits %s in displayed units, storing SI, one Undo per edit', async (kind, value, si, initial) => {
      await mount(); await choose('Series');
      await oneUndo(() => choose(`Add ${kind} (${initial})`, node()));
      await oneUndo(() => enter(node('2').querySelector('input')!, value));
      expect(network().children[2]).toMatchObject({ kind, value: expect.closeTo(si, 10) });
      expect(node('2').textContent).toContain(initial.split(' ')[1]);
    });

  it('inverts, changes connection kind, adds an unwired woofer, and removes nodes with one Undo each', async () => {
    await mount(); await choose('Parallel');
    await oneUndo(() => click(node('0').querySelector<HTMLInputElement>('input[type="checkbox"]')!));
    expect(network().children[0]).toMatchObject({ invert: true });
    await oneUndo(() => select(node().querySelector('select')!, 'series'));
    await oneUndo(() => choose('Add Woofer 3', node()));
    expect(network().children.at(-1)).toEqual({ kind: 'driver', radiator_id: third });
    await click(ownButton(node(), 'Edit…'));
    expect(document.querySelector('[role="menu"]')?.textContent).not.toContain('Add Woofer');
    await click([...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')].find(item => item.textContent === 'Remove network')!);
    expect(assembly().wiring_groups).toBeUndefined();
    await act(async () => { await controller.undo(); });
    await oneUndo(() => choose('Remove node', node('2')));
    await oneUndo(() => choose('Remove node', node('1')));
    expect(assembly().wiring_groups).toBeUndefined();
    expect(assembly().drivers?.[first]).toBeDefined();
  });

  it('wraps a driver only when its second child is chosen and collapses that connection on removal', async () => {
    await mount(); await choose('Series');
    const count = steps();
    await choose('Wrap in parallel…', node('0'));
    expect(steps()).toBe(count);
    expect(node('0').textContent).toContain('complete the parallel connection');
    await oneUndo(() => choose('Add L (1 mH)', node('0')));
    expect(network().children[0]).toEqual({ kind: 'parallel', children: [{ kind: 'driver', radiator_id: first }, { kind: 'L', value: 0.001 }] });
    await oneUndo(() => select(node('0.1').querySelector('select')!, 'C'));
    expect((network().children[0] as { children: AssemblyWiringNode[] }).children[1]).toEqual({ kind: 'C', value: expect.closeTo(0.00001, 10) });
    await oneUndo(() => choose('Remove node', node('0.1')));
    expect(network().children[0]).toEqual({ kind: 'driver', radiator_id: first });
  });

  it('commits a multi-move part-value scrub as one project Undo', async () => {
    await mount(); await choose('Series'); await choose('Add R (1 Ω)', node());
    const label = node('2').querySelector<HTMLElement>('.field-label')!;
    label.setPointerCapture = vi.fn();
    const pointer = (type: string, x: number) => act(async () => {
      label.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0, clientX: x }));
    });
    await oneUndo(async () => {
      const count = steps();
      await pointer('pointerdown', 10);
      await pointer('pointermove', 20);
      await pointer('pointermove', 30);
      expect(steps()).toBe(count);
      await pointer('pointerup', 30);
    });
    expect(network().children[2]).toEqual({ kind: 'R', value: 3 });
  });

  it('drops a pending part scrub after project Undo changes its document', async () => {
    await mount(); await choose('Series'); await choose('Add R (1 Ω)', node());
    await enter(node('2').querySelector('input')!, '2');
    const label = node('2').querySelector<HTMLElement>('.field-label')!;
    label.setPointerCapture = vi.fn();
    await act(async () => {
      label.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 10 }));
      label.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 30 }));
    });
    await act(async () => { await controller.undo(); });
    await act(async () => { label.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 30 })); });
    expect(network().children[2]).toEqual({ kind: 'R', value: 1 });
  });

  it('offers a single selected driver a deliberate passive part choice', async () => {
    selection = [first]; await mount();
    await oneUndo(() => choose('Series + R (1 Ω)'));
    expect(network()).toEqual({ kind: 'series', children: [{ kind: 'driver', radiator_id: first }, { kind: 'R', value: 1 }] });
  });

  it('keeps a rejected creation out of project history and explains how to fix member processing', async () => {
    useDesignStore.getState().replaceDocument({ ...useDesignStore.getState().design, assembly: setMemberProcessing(assembly(), second, { delay_ms: 1 }) });
    await mount(); const count = steps();
    await choose('Series');
    expect(steps()).toBe(count);
    expect(assembly().wiring_groups).toBeUndefined();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('reset member processing');
  });

  it.each(['missing driver', 'member delay', 'member invert', 'empty connection'])('locates %s validation beside its tree node', async problem => {
    let state = joinWiringDrivers(assembly(), [first, second]);
    state = setWiringGroup(state, { id: ids(), label: 'LF pair', network: { kind: 'series', children: [{ kind: 'driver', radiator_id: first }, { kind: 'driver', radiator_id: second }] } });
    const owner = state.signal_groups!.find(group => group.members.some(member => member.radiator_id === first))!;
    if (problem === 'missing driver') delete state.drivers![first];
    if (problem === 'member delay') owner.members[0].delay_ms = 1;
    if (problem === 'member invert') owner.members[0].invert = true;
    if (problem === 'empty connection') state.wiring_groups![0].network.children = [];
    useDesignStore.setState({ design: { ...useDesignStore.getState().design, assembly: state } });
    await mount();
    const alert = node(problem === 'empty connection' ? '' : '0').querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(problem === 'missing driver' ? 'assign its driver or remove' : problem === 'empty connection' ? 'add a woofer or a passive part' : 'reset member processing');
    if (problem === 'member delay' || problem === 'member invert') {
      await oneUndo(() => choose('Reset member processing', node('0')));
      expect(node('0').querySelector('[role="alert"]')).toBeNull();
    }
  });

  it('requires a selected unwired driver and rejects a nonpositive part value without history', async () => {
    selection = []; await mount();
    expect(ownButton(host, 'Wire woofers…').disabled).toBe(true);
    act(() => root.unmount()); root = createRoot(host); controller.destroy();
    selection = [first, second]; await mount(); await choose('Series'); await choose('Add R (1 Ω)', node());
    const count = steps();
    const input = node('2').querySelector<HTMLInputElement>('input')!;
    await act(async () => { input.focus(); });
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '0');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(node('2').querySelector('[role="alert"]')?.textContent).toContain('Must be greater than 0');
    await act(async () => { input.blur(); });
    expect(steps()).toBe(count);
    expect(network().children[2]).toEqual({ kind: 'R', value: 1 });
  });

  it('uses the shared driver notice for an engine without coupling and hides wiring without enabled drivers', async () => {
    useSolveOptionsStore.getState().setEngine('bempp'); await mount();
    expect(host.textContent).toContain('This engine solves every woofer at fixed velocity (1 m/s RMS), without its driver.');
    await act(async () => { useDesignStore.setState({ design: { ...useDesignStore.getState().design, assembly: {
      ...assembly(), woofers: assembly().woofers!.map(woofer => ({ ...woofer, enabled: false })),
    } } }); });
    expect(host.querySelector('[aria-label="Wiring"]')).toBeNull();
  });
});
