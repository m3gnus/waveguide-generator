import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOG_PREVIEW_BYTES, LogDialog } from './LogDialog';

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

function Harness({ jobId = 'abc' }: { jobId?: string }) {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>Log</button>
    {open && <LogDialog jobId={jobId} onClose={() => setOpen(false)}/>}
  </>;
}

describe('LogDialog', () => {
  let host: HTMLDivElement;
  let root: Root;
  let clipboardDescriptor: PropertyDescriptor | undefined;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
    else delete (navigator as { clipboard?: unknown }).clipboard;
  });

  it('fetches and renders a bounded job log preview as text', async () => {
    const fetchMock = vi.fn(async () => new Response('first line\nsecond line\n'));
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => {
      root.render(<LogDialog jobId="job one" onClose={() => undefined}/>);
      await settle();
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/jobs/job%20one/log', { signal: expect.any(AbortSignal) });
    // The dialog portals to document.body, away from the host that renders it.
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.querySelector('.log-dialog pre')?.textContent).toBe('first line\nsecond line\n');
    expect(document.querySelector('[role="dialog"]')?.getAttribute('aria-modal')).toBe('true');
    expect(document.querySelector('.job-log-content')?.getAttribute('aria-label')).toBe('Job log preview');
  });

  it('caps a declared 50 MB log at 1 MB and cancels the remaining response body', async () => {
    const cancel = vi.fn();
    let emitted = 0;
    const chunkBytes = 64 * 1024;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (emitted > LOG_PREVIEW_BYTES + chunkBytes) {
          controller.close();
          return;
        }
        controller.enqueue(new Uint8Array(chunkBytes).fill('x'.charCodeAt(0)));
        emitted += chunkBytes;
      },
      cancel,
    });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(stream, {
      headers: { 'Content-Length': '50000000', 'Content-Type': 'text/plain' },
    })));

    await act(async () => {
      root.render(<LogDialog jobId="large" onClose={() => undefined}/>);
      await settle();
    });

    const preview = document.querySelector('.job-log-content')?.textContent ?? '';
    expect(new TextEncoder().encode(preview)).toHaveLength(LOG_PREVIEW_BYTES);
    expect(document.querySelector('.log-dialog-limit')?.textContent).toContain('first 1.0 MB only');
    expect(document.querySelector<HTMLAnchorElement>('.log-dialog-actions a')?.textContent).toBe('Download complete log');
    expect(cancel).toHaveBeenCalledOnce();
    expect(emitted).toBeLessThan(50_000_000);
  });

  it('shows a clear empty state without enabling preview copy', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('')));

    await act(async () => {
      root.render(<LogDialog jobId="empty" onClose={() => undefined}/>);
      await settle();
    });

    expect(document.querySelector('.job-log-content')?.textContent).toBe('This log is empty.');
    const copy = [...document.querySelectorAll<HTMLButtonElement>('.log-dialog button')]
      .find((button) => button.textContent === 'Copy preview')!;
    expect(copy.disabled).toBe(true);
    expect(document.querySelector<HTMLAnchorElement>('.log-dialog-actions a')?.href).toContain('/api/jobs/empty/log');
  });

  it('aborts the previous preview and replaces it when Refresh is pressed', async () => {
    const signals: AbortSignal[] = [];
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, options?: RequestInit) => {
      signals.push(options?.signal as AbortSignal);
      return new Response(signals.length === 1 ? 'before refresh' : 'after refresh');
    });
    vi.stubGlobal('fetch', fetchMock);
    await act(async () => {
      root.render(<LogDialog jobId="abc" onClose={() => undefined}/>);
      await settle();
    });

    const refresh = [...document.querySelectorAll<HTMLButtonElement>('.log-dialog button')]
      .find((button) => button.textContent === 'Refresh')!;
    await act(async () => {
      refresh.click();
      await settle();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(signals[0].aborted).toBe(true);
    expect(document.querySelector('.log-dialog pre')?.textContent).toBe('after refresh');
  });

  it('copies only the fetched preview to the clipboard', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('copy this log')));
    await act(async () => {
      root.render(<LogDialog jobId="abc" onClose={() => undefined}/>);
      await settle();
    });

    const copy = [...document.querySelectorAll<HTMLButtonElement>('.log-dialog button')]
      .find((button) => button.textContent === 'Copy preview')!;
    await act(async () => { copy.click(); });

    expect(writeText).toHaveBeenCalledWith('copy this log');
    expect(document.querySelector('.log-dialog [role="status"]')?.textContent).toBe('Copied');
  });

  it('includes the log region in the focus trap, closes with Escape, and restores the opener', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('keyboard log')));
    act(() => root.render(<Harness/>));
    const opener = host.querySelector<HTMLButtonElement>('button')!;
    opener.focus();
    await act(async () => {
      opener.click();
      await settle();
    });

    const close = document.querySelector<HTMLButtonElement>('.log-dialog .dialog-close')!;
    const output = document.querySelector<HTMLElement>('.job-log-content')!;
    close.focus();
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Tab', shiftKey: true, bubbles: true, cancelable: true,
    })));
    expect(document.activeElement).toBe(output);
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })));
    expect(document.activeElement).toBe(close);
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));

    expect(document.querySelector('.log-dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
