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
  afterEach(() => { act(() => root.unmount()); vi.restoreAllMocks(); });

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
    expect(onRender).toHaveBeenCalledOnce();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(current).toMatchObject({ field: null, error: null, loading: false, frequencies: [], channels: [] });
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
