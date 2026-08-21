import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LogDialog } from './LogDialog';

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

  it('fetches and renders the complete job log as text', async () => {
    const fetchMock = vi.fn(async () => new Response('first line\nsecond line\n'));
    vi.stubGlobal('fetch', fetchMock);

    await act(async () => {
      root.render(<LogDialog jobId="job one" onClose={() => undefined}/>);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/jobs/job%20one/log');
    // The dialog portals to document.body, away from the host that renders it.
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.querySelector('.log-dialog pre')?.textContent).toBe('first line\nsecond line\n');
    expect(document.querySelector('[role="dialog"]')?.getAttribute('aria-modal')).toBe('true');
  });

  it('refetches the log when Refresh is pressed', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('before refresh'))
      .mockResolvedValueOnce(new Response('after refresh'));
    vi.stubGlobal('fetch', fetchMock);
    await act(async () => {
      root.render(<LogDialog jobId="abc" onClose={() => undefined}/>);
      await Promise.resolve();
      await Promise.resolve();
    });

    const refresh = [...document.querySelectorAll<HTMLButtonElement>('.log-dialog button')]
      .find((button) => button.textContent === 'Refresh')!;
    await act(async () => {
      refresh.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(document.querySelector('.log-dialog pre')?.textContent).toBe('after refresh');
  });

  it('copies the fetched text to the clipboard', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('copy this log')));
    await act(async () => {
      root.render(<LogDialog jobId="abc" onClose={() => undefined}/>);
      await Promise.resolve();
      await Promise.resolve();
    });

    const copy = [...document.querySelectorAll<HTMLButtonElement>('.log-dialog button')]
      .find((button) => button.textContent === 'Copy')!;
    await act(async () => { copy.click(); });

    expect(writeText).toHaveBeenCalledWith('copy this log');
    expect(document.querySelector('.log-dialog [role="status"]')?.textContent).toBe('Copied');
  });
});
