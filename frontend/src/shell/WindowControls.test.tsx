import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NativeWindowStore } from './nativeWindow';
import { WindowControls } from './WindowControls';

/**
 * A store wired to a fake pywebview host.
 *
 * The component is given the store rather than reaching for the module
 * singleton, so a test can state exactly which host it is describing instead of
 * inheriting whatever machine runs it.
 */
function storeFor(options: {
  backend?: string;
  customFrame?: boolean;
  maximized?: boolean;
  api?: Record<string, (...args: never[]) => Promise<unknown>>;
}) {
  const host = {
    pywebview: {
      platform: options.backend ?? 'edgechromium',
      api: {
        window_state: async () => ({
          customFrame: options.customFrame ?? true,
          maximized: options.maximized ?? false,
        }),
        ...options.api,
      },
    },
    navigator: { userAgent: '' },
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  } as unknown as Window & typeof globalThis;
  return new NativeWindowStore({ host });
}

let container: HTMLElement;
let root: Root;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete document.documentElement.dataset.nativeFrame;
  delete document.documentElement.dataset.nativeMaximized;
  delete document.documentElement.dataset.nativeBlurred;
});

async function render(store: NativeWindowStore, side: 'leading' | 'trailing') {
  await act(async () => {
    root.render(<WindowControls side={side} store={store}/>);
  });
  await act(async () => { await store.refresh(); });
}

describe('WindowControls', () => {
  // The dev browser still has real browser chrome, so drawing window buttons
  // there would be a second, non-functional set beside the tab's own.
  it('renders nothing in a browser', async () => {
    const host = {
      navigator: { userAgent: '' },
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    } as unknown as Window & typeof globalThis;
    await render(new NativeWindowStore({ host }), 'trailing');
    expect(container.querySelector('.window-controls')).toBeNull();
    expect(document.documentElement.dataset.nativeFrame).toBeUndefined();
  });

  // A launcher that kept the OS caption already has working buttons.
  it('renders nothing when the launcher kept the OS title bar', async () => {
    await render(storeFor({ customFrame: false }), 'trailing');
    expect(container.querySelector('.window-controls')).toBeNull();
  });

  it('puts the controls at the trailing edge on Windows', async () => {
    const store = storeFor({ backend: 'edgechromium' });
    await render(store, 'trailing');
    const group = container.querySelector('.window-controls');
    expect(group).not.toBeNull();
    expect(group?.classList.contains('window-controls-windows')).toBe(true);
    expect([...container.querySelectorAll('button')].map((b) => b.getAttribute('aria-label')))
      .toEqual(['Minimize', 'Maximize', 'Close']);
    expect(document.documentElement.dataset.nativeFrame).toBe('windows');
  });

  it('leaves the leading edge empty on Windows', async () => {
    await render(storeFor({ backend: 'edgechromium' }), 'leading');
    expect(container.querySelector('.window-controls')).toBeNull();
  });

  // Traffic lights sit at the leading edge and close comes first -- both are
  // muscle memory on macOS, and the wrong order is a mis-click, not a nit.
  it('puts the controls at the leading edge on macOS, close first', async () => {
    await render(storeFor({ backend: 'cocoa' }), 'leading');
    expect(container.querySelector('.window-controls-macos')).not.toBeNull();
    expect([...container.querySelectorAll('button')].map((b) => b.getAttribute('aria-label')))
      .toEqual(['Close', 'Minimize', 'Maximize']);
    expect(document.documentElement.dataset.nativeFrame).toBe('macos');
  });

  // Linux gets the status window rather than a native frame; if a build ever
  // reported a custom frame there, guessing a side would be worse than nothing.
  it('renders nothing on a platform with no convention to follow', async () => {
    await render(storeFor({ backend: 'gtkwebkit2' }), 'leading');
    expect(container.querySelector('.window-controls')).toBeNull();
    await render(storeFor({ backend: 'gtkwebkit2' }), 'trailing');
    expect(container.querySelector('.window-controls')).toBeNull();
  });

  it('names the maximize button for what it will do', async () => {
    await render(storeFor({ maximized: true }), 'trailing');
    const maximize = container.querySelector('.window-controls-maximize');
    expect(maximize?.getAttribute('aria-label')).toBe('Restore down');
    expect(document.documentElement.dataset.nativeMaximized).toBe('true');
  });

  it('drives each button through the bridge', async () => {
    const window_minimize = vi.fn(async () => undefined);
    const window_toggle_maximize = vi.fn(async () => ({ maximized: true }));
    const window_close = vi.fn(async () => undefined);
    const store = storeFor({ api: { window_minimize, window_toggle_maximize, window_close } });
    await render(store, 'trailing');

    for (const label of ['Minimize', 'Maximize', 'Close']) {
      const button = container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
      await act(async () => { button?.click(); });
    }
    expect(window_minimize).toHaveBeenCalledOnce();
    expect(window_toggle_maximize).toHaveBeenCalledOnce();
    expect(window_close).toHaveBeenCalledOnce();
  });

  // The attribute drives the drag region, so a stale one left on the document
  // would make the whole top bar unclickable in a browser.
  it('clears the document attributes when it unmounts', async () => {
    await render(storeFor({}), 'trailing');
    expect(document.documentElement.dataset.nativeFrame).toBe('windows');
    await act(async () => root.render(<></>));
    expect(document.documentElement.dataset.nativeFrame).toBeUndefined();
  });
});
