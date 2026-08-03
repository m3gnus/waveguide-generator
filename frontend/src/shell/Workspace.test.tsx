import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Workspace } from './Workspace';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe('Workspace', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
    localStorage.clear();
    host = document.createElement('div');
    host.style.width = '1200px';
    host.style.height = '800px';
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    vi.unstubAllGlobals();
    host.remove();
  });

  it('creates the four persisted dock panels without runtime errors', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await act(async () => {
      root.render(<Workspace resetKey={0}/>);
      await Promise.resolve();
    });
    expect(host.querySelector('.dv-dockview')).not.toBeNull();
    expect(host.textContent).toContain('Parameters');
    expect(host.textContent).toContain('Viewport');
    expect(host.textContent).toContain('Results');
    expect(host.textContent).toContain('Jobs');
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});
