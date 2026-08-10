import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DirectivityMapControls, SolveOptionsControls } from './SolveOptionsSections';

/**
 * The solve and directivity controls are not registry-driven, so the registry's
 * "every parameter is documented" gate cannot see them. This covers the same
 * ground for them: every labelled control here answers a hover.
 */
describe('solve and directivity control help', () => {
  let host: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    queryClient.clear();
    vi.useRealTimers();
  });

  const render = (node: React.ReactNode) => act(() => root.render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>));
  const hoverText = (element: Element) => {
    act(() => { element.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' })); vi.advanceTimersByTime(400); });
    const text = document.querySelector('.help-tip')?.textContent ?? '';
    act(() => { element.dispatchEvent(new PointerEvent('pointerout', { bubbles: true })); });
    return text;
  };

  it('documents every solve option', () => {
    render(<SolveOptionsControls />);
    for (const id of ['solve-engine', 'mesh-validation-mode', 'frequency-mode', 'solve-verbose']) {
      const control = host.querySelector(`#${id}`)!;
      expect(control, id).not.toBeNull();
      // The hover target is the labelled row, not the input itself.
      const row = control.closest('.select-row, .toggle-row')!;
      expect(hoverText(row).length, `${id} has no hover help`).toBeGreaterThan(40);
    }
  });

  it('documents every directivity map control', () => {
    render(<DirectivityMapControls />);
    for (const id of ['polar-angle-start', 'polar-angle-end', 'polar-angle-step', 'polar-distance', 'polar-norm-angle', 'polar-diagonal-angle']) {
      const label = host.querySelector(`label[for="${id}"]`)!;
      expect(label, id).not.toBeNull();
      expect(hoverText(label).length, `${id} has no hover help`).toBeGreaterThan(40);
    }
    expect(hoverText(host.querySelector('.axis-toggles')!)).toContain('planes through the horn axis');
    expect(hoverText(host.querySelector('.toggle-row')!)).toContain('balloon');
  });

  // `.section-body` is a container-query grid whose full-width exceptions select
  // direct children, so a help wrapper around these would silently reflow them.
  it('adds no wrapper element around the grid-positioned rows', () => {
    render(<DirectivityMapControls />);
    // The controls render as a fragment, so in the real panel these sit
    // directly under `.section-body`. Here that host is the test root.
    expect(host.querySelector('.axis-toggles')!.parentElement).toBe(host);
    expect(host.querySelector('.toggle-row')!.parentElement).toBe(host);
  });
});
