import { activityStore } from '../shell/activity';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { designForFamily, type DesignDocument } from '../stores/design';
import { expectNoJargon } from '../testing/expectNoJargon';
import { MeshTriangleEstimate, roughCount } from './MeshTriangleEstimate';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const unmount: (() => void)[] = [];

function mount() {
  const host = document.createElement('div');
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const render = (design: DesignDocument) => act(() => root.render(<QueryClientProvider client={client}><MeshTriangleEstimate design={{ ...design, mesh: { ...design.mesh } }} /></QueryClientProvider>));
  unmount.push(() => { act(() => root.unmount()); client.clear(); });
  return { host, render };
}

/** The server's answer depends only on whether the design carries a target. */
const answer = (off: number, on: number) => vi.fn<typeof fetch>(async (_url, init) => {
  const design = JSON.parse(String(init?.body)).design;
  return json({ triangle_count: design.mesh.surface_deviation_mm == null ? off : on });
});

beforeEach(() => activityStore.reset());
afterEach(() => { unmount.splice(0).forEach((stop) => stop()); vi.unstubAllGlobals(); });

it('gives two figures, because the build pass is an estimate', () => {
  expect(roughCount(3772)).toBe('3,800');
  expect(roughCount(22710)).toBe('23,000');
  expect(roughCount(998)).toBe('1,000');
});

it('states the cost of a target while Off, without presenting a count as exact', async () => {
  vi.stubGlobal('fetch', answer(4321, 0));
  const { host, render } = mount();
  render(designForFamily('OSSE'));
  await vi.waitFor(() => expect(host.textContent).toContain('about 4,300'));
  expect(host.textContent).not.toContain('4,321');
  expect(host.textContent).toContain('7 to 8× the triangles');
  expect(host.textContent).toContain('2.8× the memory');
});

it('compares a target with the same design Off and waits for the latest edit', async () => {
  const fetcher = answer(3772, 22710);
  vi.stubGlobal('fetch', fetcher);
  const { host, render } = mount();
  const design = designForFamily('OSSE');
  render(design);
  await vi.waitFor(() => expect(host.textContent).toContain('about 3,800'));
  design.mesh.surface_deviation_mm = 0.3;
  render(design);
  expect(host.textContent).toContain('calculating');
  await vi.waitFor(() => expect(host.textContent).toContain('about 23,000 or more'), { timeout: 2000 });
  expect(host.textContent).toContain('With this Off, about 3,800: at least 6× the triangles.');
  expect(host.textContent).toContain('reads low, often by half or more');
  const targets = fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body)).design.mesh.surface_deviation_mm);
  expect(targets).toContain(0.3);
  // The Off baseline is the query already answered, not a second request.
  expect(targets.filter((value) => value == null)).toHaveLength(1);
});

it('asks for no baseline while Off', async () => {
  const fetcher = answer(100, 0);
  vi.stubGlobal('fetch', fetcher);
  const { host, render } = mount();
  render(designForFamily('OSSE'));
  await vi.waitFor(() => expect(host.textContent).toContain('about 100'));
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([
  ['a mesher exception', json({ detail: 'mesh_density: boom in build_from_config' }, 422)],
  ['a bad answer', json({ triangle_count: 'many' })],
  ['a server failure', new Response('', { status: 500 })],
])('turns %s into the plain estimate failure', async (_name, response) => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response));
  const { host, render } = mount();
  render(designForFamily('OSSE'));
  await vi.waitFor(() => expect(host.textContent).toContain('not available'));
  expect(host.textContent).toContain('WG could not estimate this mesh. Open the Mesh view to build it and see its size.');
  expectNoJargon(host.textContent ?? '');
});

it('keeps framework validation text off the screen', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => json({ detail: [{ loc: ['body', 'design', 'mesh'], msg: 'Field required', type: 'missing' }] }, 422)));
  const { host, render } = mount();
  render(designForFamily('OSSE'));
  await vi.waitFor(() => expect(host.textContent).toContain('not available'));
  expect(host.textContent).not.toContain('Field required');
});

it('shows a plain sentence the server worded', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => json({ detail: 'Throat mesh resolution must be above 0 mm. Change it in Element size.' }, 422)));
  const { host, render } = mount();
  render(designForFamily('OSSE'));
  await vi.waitFor(() => expect(host.textContent).toContain('Throat mesh resolution must be above 0 mm.'));
});

it.each([false, true])('tracks a shared background triangle count and ends on failure=%s', async fail => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal('fetch', () => new Promise<Response>(done => { resolve = done; }));
  const { render } = mount();
  render(designForFamily('OSSE'));
  expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'mesh-count', background: true, cancel: expect.any(Function) }]);
  await act(async () => resolve(fail ? json({ detail: 'count failed' }, 422) : json({ triangle_count: 100 })));
  await vi.waitFor(() => expect(activityStore.getSnapshot()).toHaveLength(0));
  expect(activityStore.typicalMs('mesh-count') === null).toBe(fail);
});

it('Stop aborts the real triangle count query and removes the activity', async () => {
  let signal!: AbortSignal;
  vi.stubGlobal('fetch', (_url: unknown, init?: RequestInit) => {
    signal = init!.signal!;
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  });
  const { render } = mount();
  render(designForFamily('OSSE'));
  await act(async () => activityStore.getSnapshot()[0].cancel!());
  expect(signal.aborted).toBe(true);
  await vi.waitFor(() => expect(activityStore.getSnapshot()).toHaveLength(0));
  expect(activityStore.typicalMs('mesh-count')).toBeNull();
});
