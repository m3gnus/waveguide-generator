import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { designForFamily, type DesignDocument } from '../stores/design';
import { expectNoJargon } from '../testing/expectNoJargon';
import { MeshTriangleEstimate } from './MeshTriangleEstimate';

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

afterEach(() => { unmount.splice(0).forEach((stop) => stop()); vi.unstubAllGlobals(); });

it('shows the exact count and the cost of a target while Off', async () => {
  vi.stubGlobal('fetch', answer(4321, 0));
  const { host, render } = mount();
  render(designForFamily('OSSE'));
  await vi.waitFor(() => expect(host.textContent).toContain('4,321'));
  expect(host.textContent).toContain('7 to 8× the triangles');
  expect(host.textContent).toContain('2.8× the memory');
});

it('compares a target with the same design Off and waits for the latest edit', async () => {
  const fetcher = answer(3772, 22710);
  vi.stubGlobal('fetch', fetcher);
  const { host, render } = mount();
  const design = designForFamily('OSSE');
  render(design);
  await vi.waitFor(() => expect(host.textContent).toContain('3,772'));
  design.mesh.surface_deviation_mm = 0.3;
  render(design);
  expect(host.textContent).toContain('calculating');
  await vi.waitFor(() => expect(host.textContent).toContain('22,710'), { timeout: 2000 });
  expect(host.textContent).toContain('With this Off, 3,772: 6× the triangles.');
  expect(host.textContent).not.toContain('or more');
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
  await vi.waitFor(() => expect(host.textContent).toContain('100'));
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
  expect(host.textContent).toContain('WG could not count this mesh. Open the Mesh view to build it and see its size.');
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
