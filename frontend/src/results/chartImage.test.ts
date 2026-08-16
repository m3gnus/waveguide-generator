import { describe, expect, it, vi } from 'vitest';
import { composeChartCanvases, copyChartPng } from './chartImage';

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    x: left, y: top, left, top, width, height,
    right: left + width, bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect;
}

describe('chart PNG composition', () => {
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
    vi.spyOn(output, 'toBlob').mockImplementation((callback) => callback(blob));
    const nativeCreateElement = document.createElement.bind(document);
    const createElement = vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => (
      tagName.toLowerCase() === 'canvas' ? output : nativeCreateElement(tagName, options)
    )) as typeof document.createElement);

    let itemData: Record<string, Blob> | undefined;
    class FakeClipboardItem {
      constructor(data: Record<string, Blob>) { itemData = data; }
    }
    const clipboardItemDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'ClipboardItem');
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const write = vi.fn(async () => undefined);
    Object.defineProperty(globalThis, 'ClipboardItem', { configurable: true, value: FakeClipboardItem });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { write } });
    try {
      await copyChartPng(container, '#211f1d');
      expect(itemData?.['image/png']).toBe(blob);
      expect(write).toHaveBeenCalledWith([expect.any(FakeClipboardItem)]);
    } finally {
      createElement.mockRestore();
      if (clipboardItemDescriptor) Object.defineProperty(globalThis, 'ClipboardItem', clipboardItemDescriptor);
      else delete (globalThis as { ClipboardItem?: unknown }).ClipboardItem;
      if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
      else delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });
});
