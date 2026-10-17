import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { decodeFemFieldPayload } from './femField';
import { femFieldFixture } from './test-fixtures/femField';
import { useFemField, useFemPlane } from './useFemField';

let current: ReturnType<typeof useFemField>;
function Reader({ jobId, onRender }: { jobId: string | null; onRender?: () => void }) {
  current = useFemField(jobId); onRender?.(); return null;
}

describe('FEM result selection fencing', () => {
  let root: Root;
  let host: HTMLDivElement;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div'); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); vi.useRealTimers(); vi.restoreAllMocks(); });

  it('does not fetch or schedule another render for an inactive result', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(femFieldFixture(100, 'front'))));
    const onRender = vi.fn();
    await act(async () => { root.render(<Reader jobId={null} onRender={onRender}/>); });
    expect(onRender).toHaveBeenCalledOnce();
    expect(fetchSpy).not.toHaveBeenCalled();
    await act(async () => { root.render(<Reader jobId="fem" onRender={onRender}/>); });
    expect(current.field).not.toBeNull();
    onRender.mockClear();
    fetchSpy.mockClear();
    await act(async () => { root.render(<Reader jobId={null} onRender={onRender}/>); });
    onRender.mockClear();
    await act(async () => { root.render(<Reader jobId={null} onRender={onRender}/>); });
    expect(onRender).toHaveBeenCalledOnce();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(current).toMatchObject({ field: null, error: null, loading: false, frequencies: [], channels: [] });
  });

  it('requests and shows only the last of several rapid frequency selections before a response', async () => {
    vi.useFakeTimers();
    const fixture = (frequency: number) => ({ ...femFieldFixture(frequency), frequencies_hz: [100, 200, 500, 1000] });
    let release: (response: Response) => void = () => undefined;
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(fixture(100)))).mockImplementation(() => new Promise<Response>((resolve) => { release = resolve; }));
    await act(async () => root.render(<Reader jobId="fem"/>));
    fetchSpy.mockClear();
    for (const index of [1, 2, 3]) {
      act(() => current.setFrequencyIndex(index));
      expect(current.frequencyIndex).toBe(index);
      expect(current.field).toBeNull();
      await act(async () => vi.advanceTimersByTimeAsync(100));
      expect(fetchSpy).not.toHaveBeenCalled();
    }
    await act(async () => vi.advanceTimersByTimeAsync(149));
    expect(fetchSpy).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(String(fetchSpy.mock.calls[0][0])).toContain('frequency=1000');
    expect(current.field).toBeNull();
    await act(async () => release(new Response(JSON.stringify(fixture(1000)))));
    expect(current.field?.frequencyHz).toBe(1000);
    expect(current.loading).toBe(false);
  });

  it.each(['inactive', 'unmount', 'other-job'] as const)('cancels a waiting selection on %s', async (exit) => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(femFieldFixture())));
    await act(async () => root.render(<Reader jobId="fem"/>));
    act(() => current.setFrequencyIndex(1));
    if (exit === 'inactive') act(() => root.render(<Reader jobId={null}/>));
    else if (exit === 'unmount') act(() => root.render(null));
    else await act(async () => root.render(<Reader jobId="other"/>));
    fetchSpy.mockClear();
    await act(async () => vi.advanceTimersByTimeAsync(250));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('releases the loaded field and axes when inactive, even if the same job returns', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(femFieldFixture()))).mockImplementation(() => new Promise<Response>(() => {}));
    await act(async () => root.render(<Reader jobId="fem"/>));
    expect(current.field).not.toBeNull();
    act(() => root.render(<Reader jobId={null}/>));
    act(() => root.render(<Reader jobId="fem"/>));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(String(fetchSpy.mock.calls[1][0])).not.toContain('frequency=');
    expect(current).toMatchObject({ field: null, loading: true, frequencies: [], channels: [] });
  });

  it('aborts obsolete loads and ignores a late response after another run is selected', async () => {
    let release: (value: Response) => void = () => undefined;
    let firstSignal: AbortSignal | undefined;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, options) => {
      if (String(input).includes('/old/')) {
        firstSignal = options?.signal as AbortSignal;
        return new Promise<Response>((resolve) => { release = resolve; });
      }
      return new Response(JSON.stringify(femFieldFixture(1000, 'rear')));
    });
    act(() => root.render(<Reader jobId="old"/>));
    await act(async () => { root.render(<Reader jobId="new"/>); });
    expect(firstSignal?.aborted).toBe(true);
    expect(current.field?.frequencyHz).toBe(1000);
    await act(async () => { release(new Response(JSON.stringify(femFieldFixture(100, 'front')))); });
    expect(current.field?.frequencyHz).toBe(1000);
    expect(current.channel).toBe('rear');
    act(() => root.render(<Reader jobId={null}/>));
    expect(current.field).toBeNull();
    expect(current.channels).toEqual([]);
  });
});

let currentPlane: ReturnType<typeof useFemPlane>;
function PlaneReader({ jobId, scope, quantity, x = 0 }: { jobId: string | null;
  scope: 'interior' | 'exterior' | 'combined'; quantity: 'pressure' | 'velocity_normal'; x?: number }) {
  const payload = femFieldFixture();
  const field = { ...decodeFemFieldPayload(payload), payload };
  currentPlane = useFemPlane(jobId, field, { origin_m: [x, 0, 0], axis_u: [1, 0, 0], axis_v: [0, 1, 0],
    width_m: 1, height_m: 1, nx: 2, ny: 2 }, scope, quantity);
  return null;
}

it('coupled plane requests debounce, abort and discard obsolete results', async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  const host = document.createElement('div'), root = createRoot(host);
  const pending: Array<{ resolve: (r: Response) => void; signal: AbortSignal }> = [];
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, options) => new Promise((resolve) => {
    pending.push({ resolve, signal: options!.signal as AbortSignal });
  }));
  const reply = () => new Response(JSON.stringify({ nx: 2, ny: 2,
    points_mm: btoa(String.fromCharCode(...new Uint8Array(new Float32Array(12).buffer))),
    pressure_real: btoa(String.fromCharCode(...new Uint8Array(new Float32Array([1, 1, 1, 1]).buffer))),
    pressure_imag: btoa(String.fromCharCode(...new Uint8Array(new Float32Array(4).buffer))),
    region_class: btoa(String.fromCharCode(2, 2, 2, 2)), crosses_body: btoa(String.fromCharCode(0)),
    valid_mask: btoa(String.fromCharCode(1, 1, 1, 1)), frequency_hz: 100, channel_id: 'front',
    scope: 'combined', quantity: 'pressure', phasor_convention: 'exp(-i omega t)', unit: 'Pa' }));
  try {
    act(() => root.render(<PlaneReader jobId="one" scope="combined" quantity="pressure"/>));
    await act(async () => vi.advanceTimersByTimeAsync(250));
    expect(JSON.parse(fetchSpy.mock.calls[0][1]!.body as string).scope).toBe('combined');
    act(() => root.render(<PlaneReader jobId="one" scope="combined" quantity="pressure" x={1}/>));
    expect(pending[0].signal.aborted).toBe(true); expect(currentPlane.field).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(250));
    await act(async () => pending[1].resolve(reply()));
    expect(currentPlane.field?.real.length).toBe(6);
    await act(async () => pending[0].resolve(reply()));
    expect(currentPlane.field?.real.length).toBe(6);
    act(() => root.render(<PlaneReader jobId="one" scope="interior" quantity="velocity_normal"/>));
    await act(async () => vi.advanceTimersByTimeAsync(250));
    expect(fetchSpy).toHaveBeenCalledTimes(2); expect(currentPlane.field).toBeNull();
  } finally { act(() => root.unmount()); vi.useRealTimers(); vi.restoreAllMocks(); }
});
