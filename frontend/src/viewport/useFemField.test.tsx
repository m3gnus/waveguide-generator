import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { femFieldFixture } from './test-fixtures/femField';
import { useFemField } from './useFemField';

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
