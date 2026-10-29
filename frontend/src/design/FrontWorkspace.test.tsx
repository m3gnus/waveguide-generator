import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../sketch/adapters/frontView.fixture.json';
import type { DesignAssembly } from '../stores/assembly';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { FRONT_CONTEXT_DELAY_MS, FrontWorkspace, FrontWorkspaceLauncher, openFrontWorkspace, closeFrontWorkspace } from './FrontWorkspace';

const BEFORE = fixture.before.assembly as DesignAssembly;
const [W1] = BEFORE.woofers!.map(woofer => woofer.id);
let host: HTMLDivElement, root: Root, frames: Map<number, FrameRequestCallback>, frameId: number;
let requests: Array<Record<string, unknown>>;

const fetcher = (async (_url: string, init?: RequestInit) => {
  requests.push(JSON.parse(String(init?.body)));
  return new Response(JSON.stringify(fixture.before.context), { status: 200, headers: { 'Content-Type': 'application/json' } });
}) as typeof fetch;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  frames = new Map(); frameId = 0; requests = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  resetDesignStore();
  useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: structuredClone(BEFORE) });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount()); host.remove(); closeFrontWorkspace();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); resetDesignStore();
  window.history.replaceState(null, '', '/');
});

const svg = () => document.querySelector<SVGSVGElement>('svg[role="application"]')!;
const centre = (id: string) => {
  const hit = document.querySelector<SVGCircleElement>(`circle.sketch-point-hit[data-id="${id}"]`);
  return hit && { x: Number(hit.getAttribute('cx')), y: Number(hit.getAttribute('cy')), element: hit };
};
const pointer = (element: Element, type: string, at: { x: number; y: number }) => act(async () => {
  element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: at.x, clientY: at.y, buttons: type === 'pointerup' ? 0 : 1 }));
});
const flush = () => act(async () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); });
const button = (label: string) => [...document.querySelectorAll('button')].find(item => item.textContent === label)!;
const assembly = () => useDesignStore.getState().design.assembly!;
const steps = () => useDesignStore.temporal.getState().pastStates.length;

async function mount(onClose = vi.fn()) {
  await act(async () => root.render(<FrontWorkspace onClose={onClose} fetcher={fetcher}/>));
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
  await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
  expect(centre(W1)).not.toBeNull();
  return onClose;
}

describe('front workspace', () => {
  it('asks the server for the context of the assembly on screen', async () => {
    await mount();
    expect((requests[0].design as { assembly: DesignAssembly }).assembly).toEqual(BEFORE);
  });

  it('makes one woofer drag one Undo step and rehydrates on Undo', async () => {
    await mount();
    const start = centre(W1)!, before = steps();
    await pointer(start.element, 'pointerdown', start);
    for (const dy of [10, 20, 30]) { await pointer(svg(), 'pointermove', { x: start.x, y: start.y + dy }); await flush(); }
    await pointer(svg(), 'pointerup', { x: start.x, y: start.y + 30 });
    await act(async () => { await vi.waitFor(() => expect(useDesignStore.getState().dragSnapshot).toBeNull()); });
    const gap = assembly().woofers![0].placement.gap_mm!;
    expect(gap).toBeGreaterThan(10);
    expect(steps()).toBe(before + 1);
    // The preview follows the edit; the woofer is drawn lower on screen.
    expect(centre(W1)!.y).toBeGreaterThan(start.y);
    await act(async () => { useDesignStore.getState().undo(); });
    expect(assembly()).toEqual(BEFORE);
    expect(centre(W1)!.y).toBeCloseTo(start.y, 6);
  });

  it('makes every property action one step, keyed by identity', async () => {
    await mount();
    const before = steps();
    await act(async () => { button('Add woofer below').click(); });
    await act(async () => { await vi.waitFor(() => expect(assembly().woofers).toHaveLength(5)); });
    expect(steps()).toBe(before + 1);
    const added = assembly().woofers![4];
    expect(assembly().signal_groups!.at(-1)!.members).toEqual([{ radiator_id: added.id }]);
    await act(async () => { useDesignStore.getState().undo(); });
    expect(assembly()).toEqual(BEFORE);
  });

  it('closes when a design without an assembly is opened', async () => {
    const onClose = await mount();
    await act(async () => { useDesignStore.getState().replaceDocument(designForFamily('R-OSSE')); });
    expect(onClose).toHaveBeenCalled();
  });
});

describe('front workspace launch', () => {
  it('stays hidden unless asked for', async () => {
    await act(async () => root.render(<FrontWorkspaceLauncher/>));
    expect(host.textContent).toBe('');
    window.history.replaceState(null, '', '/?assembly=1');
    await act(async () => root.render(<FrontWorkspaceLauncher key="flagged"/>));
    expect(host.textContent).toContain('Sources and baffle');
  });

  it('gives a design without an assembly a horn-only one as one step', () => {
    useDesignStore.getState().replaceDocument(designForFamily('R-OSSE'));
    openFrontWorkspace();
    expect(assembly().woofers).toEqual([]);
    expect(assembly().signal_groups).toEqual([expect.objectContaining({ members: [{ radiator_id: 'horn-throat' }] })]);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.assembly).toBeUndefined();
  });
});
