import { adoptCurrentDesignAssembly } from '../jobs/assemblySolve.fixtures';
import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FRONT_CONTEXT_LIMIT_MS } from '../api/assembly';
import fixture from '../sketch/adapters/frontView.fixture.json';
import { addWoofer, commitAssembly, emptyAssembly, setPlacement, type DesignAssembly } from '../stores/assembly';
import { requestAssemblyBuild } from '../stores/assemblyBuild';
import { activityStore } from '../shell/activity';
import { deferred } from '../shell/cadlink/selection.fixtures';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { FRONT_CONTEXT_DELAY_MS, FrontWorkspace, FrontWorkspaceLauncher, openFrontWorkspace, closeFrontWorkspace } from './FrontWorkspace';

const solveControl = vi.hoisted(() => ({ current: null as Pick<NonNullable<ReturnType<typeof import('../shell/JobsCoordinator')['useOptionalSolveControl']>>, 'solve' | 'disabled' | 'title' | 'actionError' | 'solveNotice'> | null }));
vi.mock('../shell/JobsCoordinator', async importOriginal => ({
  ...await importOriginal<typeof import('../shell/JobsCoordinator')>(),
  useOptionalSolveControl: () => solveControl.current,
}));

const BEFORE = fixture.before.assembly as DesignAssembly;
const [W1] = BEFORE.woofers!.map(woofer => woofer.id);
let host: HTMLDivElement, root: Root, frames: Map<number, FrameRequestCallback>, frameId: number;
let requests: Array<Record<string, unknown>>;
const enclosed = () => { const design = designForFamily('R-OSSE'); design.enclosure.depth = 250; return design; };

const fetcher = (async (_url: string, init?: RequestInit) => {
  requests.push(JSON.parse(String(init?.body)));
  return new Response(JSON.stringify(fixture.before.context), { status: 200, headers: { 'Content-Type': 'application/json' } });
}) as typeof fetch;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  solveControl.current = { solve: vi.fn().mockResolvedValue('submitted'), disabled: false, title: 'Solve the prepared imported mesh', actionError: null };
  frames = new Map(); frameId = 0; requests = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  resetDesignStore();
  useDesignStore.getState().replaceDocument({ ...enclosed(), assembly: structuredClone(BEFORE) });
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
const addWooferBelow = () => act(async () => { button('Add woofer').click(); });
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
  it('enables Build after Stop on a newer overlapping build whose predecessor has settled', async () => {
    const older = deferred<Response>();
    let calls = 0;
    let operationId: string | null = null;
    const cancellations: string[] = [];
    const compile: typeof fetch = (url, init) => {
      if (init?.method === 'DELETE') {
        cancellations.push(String(url));
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (init?.method !== 'POST') return Promise.resolve(Response.json({ stages: [], total: 4 }));
      if (++calls === 1) return older.promise;
      operationId = new Headers(init.headers).get('X-WG-Operation-Id');
      return new Promise((_resolve, reject) => {
        const signal = init!.signal!;
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    };
    const first = requestAssemblyBuild(compile);
    commitAssembly(setPlacement(assembly(), W1, { anchor: 'below', gap_mm: 30 }));
    const second = requestAssemblyBuild(compile);
    older.resolve(new Response(JSON.stringify({ status: 'superseded' })));
    await first;
    activityStore.getSnapshot().at(-1)!.cancel!();
    await expect(second).resolves.toEqual({ status: 'cancelled' });
    expect(operationId).toBeTruthy();
    expect(cancellations).toEqual([`/api/operation-progress/${operationId}`]);
    await mount();
    expect(button('Build').disabled).toBe(false);
    expect(activityStore.getSnapshot()).toEqual([]);
  });

  it('submits through the shared solve control and shows the submitted words', async () => {
    const solve = vi.fn().mockResolvedValue('submitted');
    solveControl.current = { solve, disabled: false, title: 'Solve the prepared imported mesh', actionError: null };
    await mount();
    expect(button('Solve').disabled).toBe(false);
    await act(async () => button('Solve').click());
    expect(solve).toHaveBeenCalledOnce();
    expect(document.body.textContent).toContain('Solve submitted. Close this window to follow it in Results.');
  });

  it('shows the shared action error and respects the shared disabled state', async () => {
    const solve = vi.fn().mockResolvedValue('blocked');
    solveControl.current = { solve, disabled: true, title: 'Choose an engine for this mesh.', actionError: 'Choose an engine for this mesh.' };
    await mount();
    expect(button('Solve').disabled).toBe(true);
    expect(document.body.textContent).toContain('Choose an engine for this mesh.');
    expect(solve).not.toHaveBeenCalled();
  });

  it('shows an accepted solve\'s fixed-velocity notice as information while the dialog stays open', async () => {
    const notice = 'This engine solves every woofer at fixed velocity (1 m/s RMS), without its driver. To solve the drivers, set Advanced backend under Solver to Automatic, BEAT CPU or BEAT Metal.';
    const solve = vi.fn(async () => {
      solveControl.current = { ...solveControl.current!, solveNotice: { id: 1, message: notice } };
      return 'submitted' as const;
    });
    solveControl.current = { solve, disabled: false, title: 'Solve', actionError: null };
    await mount();
    expect(host.textContent).not.toContain(notice);
    await act(async () => button('Solve').click());
    expect(solve).toHaveBeenCalledOnce();
    const information = [...host.querySelectorAll('.front-messages .notice-info')].find(row => row.textContent === notice);
    expect(information?.getAttribute('role')).toBe('status');
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    expect(host.querySelector('.front-messages [role="alert"]')).toBeNull();
    expect(host.textContent).toContain('Solve submitted. Close this window to follow it in Results.');

    // The coordinator clears the notice on the next press; the dialog follows it.
    solveControl.current = { ...solveControl.current!, solveNotice: null };
    await act(async () => root.render(<FrontWorkspace onClose={vi.fn()} fetcher={fetcher}/>));
    expect(host.textContent).not.toContain(notice);
  });

  it.each([
    'No engine supports these settings. Choose another engine in Simulation.',
    'BEAT Metal is unavailable. Prepare BEAT Metal in Simulation, then press Solve.',
  ])('explains a disabled built Solve before a press with the shared title: %s', async title => {
    const solve = vi.fn().mockResolvedValue('blocked');
    solveControl.current = { solve, disabled: true, title, actionError: null };
    adoptCurrentDesignAssembly();
    await mount();
    expect(button('Solve').disabled).toBe(true);
    expect(button('Solve').title).toBe(title);
    expect(document.querySelector('.front-footer [role="status"]')!.textContent).toBe(title);
    expect(document.body.textContent).not.toContain('Ready to solve');
    expect(solve).not.toHaveBeenCalled();
    solveControl.current = { solve, disabled: false, title: 'Solve the prepared imported mesh', actionError: null };
    await act(async () => root.render(<FrontWorkspace onClose={vi.fn()} fetcher={fetcher}/>));
    expect(button('Solve').disabled).toBe(false);
    expect(button('Solve').title).toBe(solveControl.current.title);
    expect(document.querySelector('.front-footer [role="status"]')!.textContent).toBe('Built. Ready to solve.');
  });

  it('explains an unavailable Solve control even when the assembly is built', async () => {
    solveControl.current = null;
    adoptCurrentDesignAssembly();
    await mount();
    expect(button('Solve').disabled).toBe(true);
    expect(button('Solve').title).toContain('try again from the main workspace');
    expect(document.querySelector('.front-footer [role="status"]')!.textContent).toBe(button('Solve').title);
    expect(document.body.textContent).not.toContain('Ready to solve');
  });

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
    await addWooferBelow();
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

  it('removes an assembly as one step and returns the design to its plain content', async () => {
    useDesignStore.getState().replaceDocument(enclosed());
    const plain = JSON.stringify(serializeDesign(useDesignStore.getState().design));
    useDesignStore.getState().replaceDocument({ ...enclosed(), assembly: structuredClone(BEFORE) });
    const onClose = await mount();
    const before = steps();
    await act(async () => { button('Remove assembly').click(); });
    expect(useDesignStore.getState().design.assembly).toBeUndefined();
    expect(JSON.stringify(serializeDesign(useDesignStore.getState().design))).toBe(plain);
    expect(steps()).toBe(before + 1);
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => { useDesignStore.getState().undo(); });
    expect(assembly()).toEqual(BEFORE);
  });
});

describe('leaving an assembly', () => {
  it('stays removed across a family switch', async () => {
    await mount();
    await act(async () => { button('Remove assembly').click(); });
    await act(async () => { useDesignStore.getState().setFamily('OSSE'); });
    expect(useDesignStore.getState().design.assembly).toBeUndefined();
  });

  it('goes back to no assembly when the last woofer is deleted, and Undo restores it', async () => {
    useDesignStore.getState().replaceDocument(enclosed());
    const plain = JSON.stringify(serializeDesign(useDesignStore.getState().design));
    const one = addWoofer(emptyAssembly(), { anchor: 'below' });
    useDesignStore.getState().replaceDocument({ ...enclosed(), assembly: one.assembly });
    await act(async () => root.render(<FrontWorkspace onClose={vi.fn()} fetcher={fetcher}/>));
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
    await act(async () => { document.querySelector<HTMLButtonElement>('button.front-source')!.click(); });
    await act(async () => { svg().focus(); svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true })); });
    await act(async () => { await vi.waitFor(() => expect(useDesignStore.getState().design.assembly).toBeUndefined()); });
    expect(JSON.stringify(serializeDesign(useDesignStore.getState().design))).toBe(plain);
    await act(async () => { useDesignStore.getState().undo(); });
    expect(assembly().woofers).toHaveLength(1);
  });
});

describe('looking at a design without an assembly', () => {
  const plainDesign = () => {
    useDesignStore.getState().replaceDocument(enclosed());
    const { designRevision, design } = useDesignStore.getState();
    return { designRevision, content: JSON.stringify(serializeDesign(design)), past: steps() };
  };
  const unchanged = (before: ReturnType<typeof plainDesign>) => {
    const { designRevision, design } = useDesignStore.getState();
    expect(design.assembly).toBeUndefined();
    expect(designRevision).toBe(before.designRevision);
    expect(JSON.stringify(serializeDesign(design))).toBe(before.content);
    expect(steps()).toBe(before.past);
  };
  const mountPlain = async () => {
    await act(async () => root.render(<FrontWorkspace onClose={vi.fn()} fetcher={fetcher}/>));
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
  };

  it('leaves the design untouched by opening and closing', async () => {
    const before = plainDesign();
    openFrontWorkspace();
    await mountPlain();
    closeFrontWorkspace();
    await act(async () => root.render(null));
    unchanged(before);
  });

  it('draws the front view from a horn-only assembly the design does not hold', async () => {
    plainDesign();
    await mountPlain();
    expect((requests[0].design as { assembly: DesignAssembly }).assembly.woofers).toEqual([]);
    expect(button('Build').disabled).toBe(true);
    expect(button('Solve').disabled).toBe(true);
  });

  it('writes the assembly on the first real edit, and Undo takes it back out', async () => {
    const before = plainDesign();
    await mountPlain();
    await addWooferBelow();
    await act(async () => { await vi.waitFor(() => expect(assembly().woofers).toHaveLength(1)); });
    expect(steps()).toBe(before.past + 1);
    await act(async () => { useDesignStore.getState().undo(); });
    expect(useDesignStore.getState().design.assembly).toBeUndefined();
    expect(JSON.stringify(serializeDesign(useDesignStore.getState().design))).toBe(before.content);
  });
});

describe('front workspace status', () => {
  const CHECKING = 'Checking that the woofers fit on the baffle…', UNCHECKED = 'WG could not check the fit. Build and Solve check it themselves.';
  const status = () => document.querySelector('.front-footer [role="status"]')!.textContent;
  const failing = (status: number, body: unknown = {}) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
  const mountWith = async (with_fetcher: typeof fetch) => {
    await act(async () => root.render(<FrontWorkspace onClose={vi.fn()} fetcher={with_fetcher}/>));
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
  };

  it('never shows a transport failure as the message', async () => {
    await mountWith(failing(502));
    expect(document.body.textContent).toContain('WG could not show the front view');
    expect(document.body.textContent).toContain('WG could not update the front view');
    expect(document.body.textContent).not.toMatch(/502|unavailable \(/);
  });

  it('shows the words of a refusal the server gives', async () => {
    await mountWith(failing(422, { error: { code: 'assembly_refused', message: 'Set an enclosure depth.' } }));
    expect(document.body.textContent).toContain('Set an enclosure depth.');
  });

  it('retries the front view from its empty state', async () => {
    let answers = 0;
    await mountWith((async (url: string, init?: RequestInit) => ++answers === 1 ? new Response('', { status: 502 }) : fetcher(url, init)) as typeof fetch);
    await act(async () => { button('Try again').click(); });
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
    expect(document.body.textContent).not.toContain('WG could not show the front view');
    expect(centre(W1)).not.toBeNull();
  });

  it('says what a clearance problem is and marks the woofer in the list', async () => {
    const context = { ...fixture.before.context, minimum_clearance_mm: 0.1, clearances: { [W1]: { mouth_mm: -3, woofers: {} } } };
    await mountWith((async () => new Response(JSON.stringify(context), { status: 200 })) as unknown as typeof fetch);
    expect(document.body.textContent).toContain('Woofer 1 overlaps the mouth by 3 mm. Raise Gap to mouth, or move it.');
    expect(document.querySelector('button.front-source.problem')?.textContent).toContain('Woofer 1');
  });

  it('keeps a woofer that does not fit away from Build and Solve', async () => {
    const context = { ...fixture.before.context, minimum_clearance_mm: 0.1, clearances: { [W1]: { mouth_mm: 10, baffle_edge_mm: -30, woofers: {} } } };
    await mountWith((async () => new Response(JSON.stringify(context), { status: 200 })) as unknown as typeof fetch);
    expect(document.body.textContent).toContain('Does not fit');
    expect(status()).toContain('Reduce Radius');
    expect(button('Build').disabled).toBe(true);
    expect(button('Solve').disabled).toBe(true);
    expect(button('Build').title).toContain('Reduce Radius');
  });

  it('holds Build and Solve until the fit of what is on screen is known', async () => {
    let answer!: (response: Response) => void;
    const pending = (() => new Promise<Response>(resolve => { answer = resolve; })) as unknown as typeof fetch;
    await act(async () => root.render(<FrontWorkspace onClose={vi.fn()} fetcher={pending}/>));
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
    expect(status()).toBe(CHECKING);
    expect(button('Build').disabled).toBe(true);
    expect(button('Solve').disabled).toBe(true);
    await act(async () => { answer(new Response(JSON.stringify(fixture.before.context), { status: 200 })); });
    expect(button('Build').disabled).toBe(false);
    // An edit makes that answer old at once, before the next one arrives.
    act(() => useDesignStore.getState().updateValue('enclosure.space_b', 40));
    expect(button('Build').disabled).toBe(true);
    expect(status()).toBe(CHECKING);
  });

  it('asks again when the revision moves on, even with the same design', async () => {
    await mountWith(fetcher);
    expect(button('Build').disabled).toBe(false);
    const asked = requests.length;
    act(() => useDesignStore.setState(state => ({ designRevision: state.designRevision + 1 })));
    expect(button('Build').disabled).toBe(true);
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
    expect(requests.length).toBe(asked + 1);
    expect(button('Build').disabled).toBe(false);
  });

  it('holds Build and Solve no longer than the limit when the check never answers', async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const silent = ((_url: string, init?: RequestInit) => { signals.push(init!.signal!); return new Promise<Response>(() => {}); }) as typeof fetch;
    await act(async () => root.render(<FrontWorkspace onClose={vi.fn()} fetcher={silent}/>));
    await act(() => vi.advanceTimersByTimeAsync(FRONT_CONTEXT_DELAY_MS + FRONT_CONTEXT_LIMIT_MS - 1));
    expect(button('Build').disabled).toBe(true);
    expect(status()).toBe(CHECKING);
    await act(() => vi.advanceTimersByTimeAsync(2));
    expect(signals[0].aborted).toBe(true);
    expect(status()).toBe(UNCHECKED);
    expect(button('Build').disabled).toBe(false);
    expect(button('Try again')).toBeTruthy();
  });

  it('checks again from Try again, and a refusal that answer brings holds', async () => {
    const context = { ...fixture.before.context, minimum_clearance_mm: 0.1, clearances: { [W1]: { mouth_mm: -3, woofers: {} } } };
    let answers = 0;
    await mountWith((async () => ++answers === 1 ? new Response('', { status: 502 }) : new Response(JSON.stringify(context), { status: 200 })) as unknown as typeof fetch);
    expect(button('Build').disabled).toBe(false);
    await act(async () => { button('Try again').click(); });
    expect(button('Build').disabled).toBe(true);
    expect(status()).toBe(CHECKING);
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
    expect(status()).toContain('overlaps the mouth');
    expect(button('Build').disabled).toBe(true);
  });

  it('does not refuse on an answer about an earlier revision', async () => {
    const context = { ...fixture.before.context, minimum_clearance_mm: 0.1, clearances: { [W1]: { mouth_mm: -3, woofers: {} } } };
    let answers = 0;
    await mountWith((async () => ++answers === 1 ? new Response(JSON.stringify(context), { status: 200 }) : new Response('', { status: 502 })) as unknown as typeof fetch);
    expect(button('Build').disabled).toBe(true);
    act(() => useDesignStore.getState().updateValue('enclosure.space_b', 40));
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
    expect(status()).toBe(UNCHECKED);
    expect(button('Build').disabled).toBe(false);
  });

  it('keeps Build and Solve held by a refusal, with the reason beside them', async () => {
    await mountWith(failing(422, { error: { code: 'assembly_refused', message: 'Set an enclosure depth.' } }));
    expect(status()).toBe('Set an enclosure depth.');
    expect(button('Build').disabled).toBe(true);
    expect(button('Solve').disabled).toBe(true);
  });

  it('leaves the fit to the build when it cannot be checked', async () => {
    await mountWith((async () => new Response('', { status: 502 })) as unknown as typeof fetch);
    expect(status()).toBe(UNCHECKED);
    expect(button('Build').disabled).toBe(false);
  });

  it('starts the build and closes on Build, so the viewport shows it', async () => {
    const onClose = await mount();
    await act(async () => { button('Build').click(); });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(requests.some(body => 'design_revision' in body)).toBe(true);
  });

  it('offers the margin that makes room for a first woofer, and only applies it when asked', async () => {
    useDesignStore.getState().replaceDocument({ ...enclosed() });
    const context = { ...fixture.before.context, woofers: [], clearances: {}, minimum_clearance_mm: 0.1, mouth_top_mm: 140, mouth_bottom_mm: -140,
      mouth_outline_mm: Array.from({ length: 64 }, (_, index) => [140 * Math.cos(index / 32 * Math.PI), 140 * Math.sin(index / 32 * Math.PI)]),
      baffle: { x0_mm: -165, x1_mm: 165, y0_mm: -165, y1_mm: 165, edge_mm: 18 } };
    await mountWith((async () => new Response(JSON.stringify(context), { status: 200 })) as unknown as typeof fetch);
    expect(document.body.textContent).toContain('No room for a woofer yet');
    expect(useDesignStore.getState().design.enclosure.space_b).toBe(25);
    // Add creates nothing that does not fit: it opens the places, and only Free position is open.
    await act(async () => { button('Add woofer').click(); });
    expect(useDesignStore.getState().design.assembly?.woofers ?? []).toEqual([]);
    expect(document.body.textContent).toContain('No room for a woofer yet');
    const places = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')];
    expect(places.map(item => [item.textContent?.split('A 160')[0], item.getAttribute('aria-disabled') === 'true' || item.disabled])).toEqual([
      ['Above the mouth', true], ['Below the mouth', true], ['Free position', false]]);
    await act(async () => { button('Set Bottom margin to 189 mm').click(); });
    expect(useDesignStore.getState().design.enclosure.space_b).toBe(189);
  });

  it('survives a Strict Mode remount and keeps editing', async () => {
    await act(async () => root.render(<StrictMode><FrontWorkspace onClose={vi.fn()} fetcher={fetcher}/></StrictMode>));
    await act(() => new Promise(resolve => setTimeout(resolve, FRONT_CONTEXT_DELAY_MS + 50)));
    await addWooferBelow();
    await act(async () => { await vi.waitFor(() => expect(assembly().woofers).toHaveLength(5)); });
  });

  it('brings an enclosure back at the latest depth it had', async () => {
    await mountWith(fetcher);
    await act(async () => { useDesignStore.getState().updateValues({ 'enclosure.depth': 400 }); });
    await act(async () => { useDesignStore.getState().updateValues({ 'enclosure.depth': 0 }); });
    await act(async () => { button('Use an enclosure').click(); });
    expect(useDesignStore.getState().design.enclosure.depth).toBe(400);
  });

  it('keeps Add woofer closed without an enclosure and offers one', async () => {
    useDesignStore.getState().replaceDocument(designForFamily('R-OSSE'));
    await mountWith(fetcher);
    expect(document.querySelector<HTMLButtonElement>('.action-menu-primary')!.disabled).toBe(true);
    await act(async () => { button('Use an enclosure').click(); });
    expect(useDesignStore.getState().design.enclosure.depth).toBeGreaterThan(0);
    expect(useDesignStore.getState().design.assembly).toBeUndefined();
    expect(document.querySelector<HTMLButtonElement>('.action-menu-primary')!.disabled).toBe(false);
  });
});

describe('front workspace launch', () => {
  it('launches normally with its experimental badge and explanation', async () => {
    await act(async () => root.render(<FrontWorkspaceLauncher/>));
    expect(host.textContent).toContain('4 woofers on the front baffle');
    await act(async () => { useDesignStore.getState().replaceDocument(designForFamily('R-OSSE')); });
    expect(host.textContent).toContain('Sources and baffle');
    expect(host.textContent).toContain('Experimental');
    expect(host.textContent).toContain("Woofers on the enclosure's front baffle, solved with the horn.");
    await act(async () => { button('Sources and baffle…').click(); });
    expect(useDesignStore.getState().design.assembly).toBeUndefined();
  });

});
