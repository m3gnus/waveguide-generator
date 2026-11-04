import { afterEach, describe, expect, it, vi } from 'vitest';
import { composeChartCanvases, copyChartPng, saveChartPng } from './chartImage';
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

  it('encodes the captured canvas and writes the identical blob under one export activity', async () => {
    const captured = document.createElement('canvas');
    let finishEncoding!: BlobCallback;
    vi.spyOn(captured, 'toBlob').mockImplementation((callback) => { finishEncoding = callback; });
    let finishWriting!: (value: { directory: string }) => void;
    workspace.write.mockImplementation(() => new Promise((resolve) => { finishWriting = resolve; }));
    const confirm = vi.fn(async () => true);
    const saving = saveChartPng(captured, 'A.png', 'chosen', confirm);
    const [encoding] = activityStore.getSnapshot();
    expect(encoding).toMatchObject({ kind: 'export', label: 'Encoding chart PNG' });
    expect(workspace.write).not.toHaveBeenCalled();
    const blob = new Blob(['captured A pixels'], { type: 'image/png' });
    finishEncoding(blob);
    await Promise.resolve();
    expect(activityStore.getSnapshot()).toMatchObject([{ id: encoding.id, label: 'Writing chart PNG' }]);
    expect(workspace.write).toHaveBeenCalledWith('', [{ filename: 'A.png', blob }], expect.any(Function), 'confirm', 'chosen', confirm);
    expect(workspace.write.mock.calls[0][1][0].blob).toBe(blob);
    finishWriting({ directory: 'chosen directory' });
    await expect(saving).resolves.toBe('chosen directory');
    expect(activityStore.getSnapshot()).toEqual([]);
  });

  it('ends a failed encoding activity without writing a file', async () => {
    const captured = document.createElement('canvas');
    vi.spyOn(captured, 'toBlob').mockImplementation((callback) => { callback(null); });
    await expect(saveChartPng(captured, 'A.png', 'chosen')).rejects.toThrow('could not make an image');
    expect(workspace.write).not.toHaveBeenCalled();
    expect(activityStore.getSnapshot()).toEqual([]);
  });

  it('writes the composited blob to the image clipboard as PNG', async () => {
    const container = document.createElement('div');
    const layer = document.createElement('canvas');
    container.append(layer);
    layer.width = 200; layer.height = 100;
    vi.spyOn(container, 'getBoundingClientRect').mockReturnValue(rect(0, 0, 100, 50));
    vi.spyOn(layer, 'getBoundingClientRect').mockReturnValue(rect(0, 0, 100, 50));

    const output = document.createElement('canvas');
    const context = { setTransform: vi.fn(), fillRect: vi.fn(), drawImage: vi.fn(), fillStyle: '' };
    vi.spyOn(output, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
    const blob = new Blob(['png'], { type: 'image/png' });
    let finishEncoding!: BlobCallback;
    vi.spyOn(output, 'toBlob').mockImplementation((callback) => { finishEncoding = callback; });
    const nativeCreateElement = document.createElement.bind(document);
    const createElement = vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => (
      tagName.toLowerCase() === 'canvas' ? output : nativeCreateElement(tagName, options)
    )) as typeof document.createElement);

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
      const copying = copyChartPng(container, '#211f1d');
      expect(write).toHaveBeenCalledWith([expect.any(FakeClipboardItem)]);
      expect(itemData?.['image/png']).toBeInstanceOf(Promise);
      finishEncoding(blob);
      await copying;
      expect(await itemData?.['image/png']).toBe(blob);
    } finally {
      createElement.mockRestore();
      if (clipboardItemDescriptor) Object.defineProperty(globalThis, 'ClipboardItem', clipboardItemDescriptor);
      else delete (globalThis as { ClipboardItem?: unknown }).ClipboardItem;
      if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
      else delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });
});
