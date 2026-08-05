import { act, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import { ChartErrorBoundary } from './EChart';

describe('chart loading fallback', () => {
  it('keeps the results panel usable when the lazy chart chunk fails', () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    function BrokenChart(): ReactNode {
      throw new Error('chart chunk unavailable');
    }

    act(() => root.render(<ChartErrorBoundary label="Directivity"><BrokenChart/></ChartErrorBoundary>));

    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Interactive chart unavailable');
    expect(host.querySelector('[aria-label="Directivity unavailable"]')).not.toBeNull();
    act(() => root.unmount());
    consoleError.mockRestore();
    host.remove();
  });
});
