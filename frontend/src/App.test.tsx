import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppErrorBoundary } from './App';

function Bomb(): never {
  throw new Error('Directivity sweep end must be greater than its start.');
}

describe('the app-level error boundary', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    // React logs the caught error and its component stack; that noise is the
    // expected behavior under test, not a failure.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it('shows a visible message instead of a blank window when render throws', () => {
    act(() => {
      root.render(<AppErrorBoundary><Bomb /></AppErrorBoundary>);
    });
    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain('rendering error');
    expect(alert?.textContent).toContain('Directivity sweep end must be greater than its start.');
    expect(container.querySelector('button')?.textContent).toBe('Reload');
  });

  it('renders its children untouched when nothing throws', () => {
    act(() => {
      root.render(<AppErrorBoundary><span data-testid="fine">all good</span></AppErrorBoundary>);
    });
    expect(container.querySelector('[data-testid="fine"]')?.textContent).toBe('all good');
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
