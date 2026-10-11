import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { EngineCapability } from '../jobs/actions';
import { BeatRuntimePreparation } from './BeatRuntimePreparation';

let host: HTMLDivElement;
let root: Root;
const refresh = vi.fn();
const engine = (state: EngineCapability['runtime_state'], extra: Partial<EngineCapability> = {}): EngineCapability => ({
  name: 'beat-cuda', label: 'BEAT · CUDA', available: state === 'ready',
  reason: state === 'failed' ? 'Download failed: network unavailable.' : 'Runtime has not been prepared.',
  version: null, fast_paths: [], runtime_state: state,
  runtime_preparable: state === 'unprepared' || state === 'failed', ...extra,
});

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  refresh.mockReset();
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
const render = (engines: EngineCapability[], selected = 'auto') => act(() => {
  root.render(<BeatRuntimePreparation engines={engines} selected={selected} refreshCapabilities={refresh} />);
});

it.each(['unprepared', 'failed'] as const)('shows readable %s reasons and starts preparation only on click', async (state) => {
  const fetcher = vi.fn(async () => new Response('{}', { status: 202 }));
  vi.stubGlobal('fetch', fetcher);
  render([engine(state)]);
  expect(fetcher).not.toHaveBeenCalled();
  expect(host.textContent).toContain(engine(state).reason);
  const button = host.querySelector('button')!;
  expect(button.textContent).toContain(state === 'failed' ? 'Retry' : 'Prepare');
  await act(async () => { button.click(); });
  expect(fetcher).toHaveBeenCalledWith('/api/runtimes/beat/cuda/prepare', { method: 'POST' });
  expect(refresh).toHaveBeenCalledOnce();
  render([engine('preparing', { reason: 'Resolving CUDA artifacts (step: instantiate).' })]);
  expect(host.textContent).toContain('Resolving CUDA artifacts');
  expect(host.querySelector('button')).toBeNull();
  render([engine('ready')], 'beat-cuda');
  expect(host.textContent).toContain('ready');
  expect(host.querySelector('button')).toBeNull();
});

it('disables other preparation buttons while a runtime is preparing', () => {
  render([engine('preparing'), engine('unprepared', { name: 'beat-cpu', label: 'BEAT · CPU' })]);
  expect(host.querySelector('button')!.disabled).toBe(true);
  expect(host.textContent).toContain('several minutes');
});

it('explains the selected unavailable hardware without offering a button', () => {
  render([engine('unavailable', { reason: 'No supported CUDA device.' })], 'beat-cuda');
  expect(host.textContent).toContain('No supported CUDA device.');
  expect(host.querySelector('button')).toBeNull();
});

it('explains disabled preparation for the selected runtime without offering a button', () => {
  render([engine('unprepared', { name: 'beat-cpu', runtime_preparable: false, reason: 'Preparation disabled.' })], 'beat-cpu');
  expect(host.textContent).toContain('Preparation disabled.');
  expect(host.querySelector('button')).toBeNull();
});

it('hides unrelated hardware-unavailable and ready runtimes while showing actionable rows', () => {
  render([engine('unavailable', { reason: 'No supported CUDA device.' }),
    engine('unavailable', { name: 'beat-rocm', reason: 'No supported ROCm device.' }),
    engine('ready', { name: 'beat-metal', label: 'BEAT · Metal' }),
    engine('unprepared', { name: 'beat-cpu', label: 'BEAT · CPU' })]);
  expect(host.textContent).not.toContain('No supported');
  expect(host.textContent).not.toContain('BEAT · Metal');
  expect(host.querySelector('button')!.textContent).toContain('BEAT · CPU');
  render([engine('unavailable'), engine('ready', { name: 'beat-metal' })]);
  expect(host.querySelector('.beat-runtime-preparation')).toBeNull();
});

it.each([400, 409, 500])('surfaces a %s rejection and refreshes the shared lifecycle', async (status) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Preparation already running.' }), { status })));
  render([engine('failed')]);
  await act(async () => { host.querySelector('button')!.click(); });
  expect(host.querySelector('[role="alert"]')!.textContent).toBe('Preparation already running.');
  expect(refresh).toHaveBeenCalledOnce();
  expect(host.querySelector('button')!.disabled).toBe(false);
});

it.each(['preparing', 'ready', 'failed'] as const)('clears a conflict after capabilities change to %s', async (state) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Preparation already running.' }), { status: 409 })));
  render([engine('unprepared')]);
  await act(async () => { host.querySelector('button')!.click(); });
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  render([engine('unprepared')]);
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  render([engine(state)], 'beat-cuda');
  expect(host.querySelector('[role="alert"]')).toBeNull();
});


it('ends the starting state when a fast retry returns the same failure payload', async () => {
  let finish: (value: Response) => void = () => {};
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
  render([engine('failed')]);
  await act(async () => { host.querySelector('button')!.click(); });
  expect(host.textContent).toContain('Starting runtime preparation');
  await act(async () => { finish(new Response('{}', { status: 202 })); });
  expect(host.textContent).toContain('Download failed: network unavailable.');
  expect(host.querySelector('button')!.textContent).toContain('Retry');
  expect(host.querySelector('button')!.disabled).toBe(false);
});
