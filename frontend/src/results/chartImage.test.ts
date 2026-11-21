import { afterEach, describe, expect, it, vi } from 'vitest';
import { composeChartCanvases, copyChartPng, drawChartPng, pngBlobFromDataUri, renderChartSpec, saveChartPng } from './chartImage';
import { activityStore } from '../shell/activity';

const workspace = vi.hoisted(() => ({ write: vi.fn() }));
vi.mock('../api/workspace', () => ({ writeToOutputFolder: workspace.write }));

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    x: left, y: top, left, top, width, height,
    right: left + width, bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect;
}

describe('chart PNG composition', () => {
  afterEach(() => { vi.restoreAllMocks(); workspace.write.mockReset(); activityStore.reset(); });
  it('flattens every positioned canvas layer at the highest bounded pixel ratio', () => {
    const container = document.createElement('div');
    const base = document.createElement('canvas');
    const overlay = document.createElement('canvas');
    container.append(base, overlay);
    base.width = 200; base.height = 100;
    overlay.width = 150; overlay.height = 150;
    vi.spyOn(container, 'getBoundingClientRect').mockReturnValue(rect(10, 20, 100, 50));
    vi.spyOn(base, 'getBoundingClientRect').mockReturnValue(rect(10, 20, 100, 50));
    vi.spyOn(overlay, 'getBoundingClientRect').mockReturnValue(rect(60, 20, 50, 50));

    const context = {
      setTransform: vi.fn(), fillRect: vi.fn(), drawImage: vi.fn(), fillStyle: '',
    };
    const output = document.createElement('canvas');
    vi.spyOn(output, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);

    expect(composeChartCanvases(container, '#211f1d', () => output)).toBe(output);
    expect(output.width).toBe(300);
    expect(output.height).toBe(150);
    expect(context.setTransform).toHaveBeenCalledWith(3, 0, 0, 3, 0, 0);
    expect(context.fillStyle).toBe('#211f1d');
    expect(context.fillRect).toHaveBeenCalledWith(0, 0, 100, 50);
    expect(context.drawImage.mock.calls).toEqual([
      [base, 0, 0, 100, 50],
      [overlay, 50, 0, 50, 50],
    ]);
  });

  it('refuses to manufacture an empty PNG for a chart stub', () => {
    const container = document.createElement('div');
    expect(() => composeChartCanvases(container)).toThrow('no image');
  });

  it('writes the drawn blob under one export activity', async () => {
    let finishDrawing!: (blob: Blob) => void;
    const image = new Promise<Blob>((resolve) => { finishDrawing = resolve; });
    let finishWriting!: (value: { directory: string }) => void;
    workspace.write.mockImplementation(() => new Promise((resolve) => { finishWriting = resolve; }));
    const confirm = vi.fn(async () => true);
    const saving = saveChartPng(image, 'A.png', 'chosen', confirm);
    const [writing] = activityStore.getSnapshot();
    expect(writing).toMatchObject({ kind: 'export', label: 'Writing chart PNG' });
    expect(workspace.write).not.toHaveBeenCalled();
    const blob = new Blob(['drawn A pixels'], { type: 'image/png' });
    finishDrawing(blob);
    await Promise.resolve();
    await Promise.resolve();
    expect(activityStore.getSnapshot()).toMatchObject([{ id: writing.id, label: 'Writing chart PNG' }]);
    expect(workspace.write).toHaveBeenCalledWith('', [{ filename: 'A.png', blob }], expect.any(Function), 'confirm', 'chosen', confirm);
    expect(workspace.write.mock.calls[0][1][0].blob).toBe(blob);
    finishWriting({ directory: 'chosen directory' });
    await expect(saving).resolves.toBe('chosen directory');
    expect(activityStore.getSnapshot()).toEqual([]);
  });

  it('draws on the status bar and stops the request when its export is abandoned', async () => {
    const seen: Array<AbortSignal | undefined> = [];
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      seen.push(init?.signal ?? undefined);
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      });
    });
    const cancel = new AbortController();
    const image = drawChartPng((scoped) => scoped('/api/render-chart', { method: 'POST' }).then(() => new Blob()), cancel.signal);
    expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'export', label: 'Drawing chart PNG' }]);
    expect(seen[0]?.aborted).toBe(false);
    cancel.abort();
    await expect(image).rejects.toThrow();
    expect(seen[0]?.aborted).toBe(true);
    expect(activityStore.getSnapshot()).toEqual([]);
    fetcher.mockRestore();
  });

  it('ends a failed drawing activity without writing a file', async () => {
    const image = Promise.reject(new Error('WG could not draw this chart image. Try again.'));
    await expect(saveChartPng(image, 'A.png', 'chosen')).rejects.toThrow('could not draw');
    expect(workspace.write).not.toHaveBeenCalled();
    expect(activityStore.getSnapshot()).toEqual([]);
  });

  it('starts the clipboard write in the gesture and hands it the pending image', async () => {
    const blob = new Blob(['png'], { type: 'image/png' });
    let finishDrawing!: (blob: Blob) => void;
    const draw = vi.fn(() => new Promise<Blob>((resolve) => { finishDrawing = resolve; }));
    let itemData: Record<string, Promise<Blob>> | undefined;
    class FakeClipboardItem {
      constructor(data: Record<string, Promise<Blob>>) { itemData = data; }
    }
    const clipboardItemDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'ClipboardItem');
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const write = vi.fn(async () => { await itemData?.['image/png']; });
    Object.defineProperty(globalThis, 'ClipboardItem', { configurable: true, value: FakeClipboardItem });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { write } });
    try {
      const copying = copyChartPng(draw);
      expect(draw).toHaveBeenCalledTimes(1);
      expect(write).toHaveBeenCalledWith([expect.any(FakeClipboardItem)]);
      expect(itemData?.['image/png']).toBeInstanceOf(Promise);
      finishDrawing(blob);
      await copying;
      expect(await itemData?.['image/png']).toBe(blob);
    } finally {
      if (clipboardItemDescriptor) Object.defineProperty(globalThis, 'ClipboardItem', clipboardItemDescriptor);
      else delete (globalThis as { ClipboardItem?: unknown }).ClipboardItem;
      if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
      else delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });

  it('asks hornlab-plots for a spec in the export theme and decodes its PNG', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ image: 'data:image/png;base64,iVBORw==' }), { status: 200 }));
    const spec = { kind: 'lines' as const, title: 'DI', x: { scale: 'log' as const }, y: {}, series: [] };
    const image = await renderChartSpec(spec, 'classic', fetcher as unknown as typeof fetch);
    expect(fetcher).toHaveBeenCalledWith('/api/render-chart', expect.objectContaining({ method: 'POST' }));
    expect(JSON.parse((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)).toEqual({ spec, theme: 'classic' });
    const blob = pngBlobFromDataUri(image);
    expect(blob.type).toBe('image/png');
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([0x89, 0x50, 0x4e, 0x47]);
  });

  it('says what failed when the renderer refuses a chart', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: 'This chart has no values to draw. Choose a chart with data, then export again.' }), { status: 422 }));
    await expect(renderChartSpec({ kind: 'lines', title: '', x: {}, y: {}, series: [] }, 'classic', fetcher as unknown as typeof fetch))
      .rejects.toThrow('no values to draw');
  });
});
