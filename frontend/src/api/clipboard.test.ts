import { afterEach, describe, expect, it, vi } from 'vitest';
import { writePendingClipboardItem } from './clipboard';

describe('pending clipboard items', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it.each(['image/png', 'text/plain'])('starts writing %s before its blob settles', async (mimeType) => {
    class PendingItem {
      constructor(public data: Record<string, Promise<Blob>>) {}
    }
    vi.stubGlobal('ClipboardItem', PendingItem);
    let encode!: (blob: Blob) => void;
    const pending = new Promise<Blob>((resolve) => { encode = resolve; });
    const write = vi.fn(async (items: PendingItem[]) => { await items[0].data[mimeType]; });
    vi.stubGlobal('navigator', { clipboard: { write } });
    const copying = writePendingClipboardItem(mimeType, pending);
    expect(write).toHaveBeenCalledOnce();
    expect(write.mock.calls[0][0][0].data[mimeType]).toBe(pending);
    const blob = new Blob(['unchanged content'], { type: mimeType });
    encode(blob);
    await copying;
    expect(await write.mock.calls[0][0][0].data[mimeType]).toBe(blob);
  });

  it('consumes a late encoding rejection even when a refused write ignores the data', async () => {
    vi.stubGlobal('ClipboardItem', class { constructor(public data: unknown) {} });
    const denied = new DOMException('Clipboard access denied.', 'NotAllowedError');
    vi.stubGlobal('navigator', { clipboard: { write: vi.fn().mockRejectedValue(denied) } });
    let fail!: (reason: unknown) => void;
    const pending = new Promise<Blob>((_, reject) => { fail = reject; });
    await expect(writePendingClipboardItem('image/png', pending)).rejects.toBe(denied);
    fail(new Error('Encoding failed after clipboard access was refused.'));
    await new Promise((resolve) => { setTimeout(resolve, 0); });
  });
});
