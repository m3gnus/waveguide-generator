import { describe, expect, it, vi } from 'vitest';
import { NativeWindowStore, platformFromBackend } from './nativeWindow';

/** A stand-in for the pywebview host, with no real bridge and no real window. */
function fakeHost(options: {
  bridge?: unknown;
  userAgent?: string;
} = {}) {
  const listeners = new Map<string, Set<() => void>>();
  const host = {
    pywebview: options.bridge,
    navigator: { userAgent: options.userAgent ?? '' },
    addEventListener: (type: string, handler: () => void) => {
      const set = listeners.get(type) ?? new Set();
      set.add(handler);
      listeners.set(type, set);
    },
    removeEventListener: (type: string, handler: () => void) => {
      listeners.get(type)?.delete(handler);
    },
  } as unknown as Window & typeof globalThis;
  const fire = (type: string) => {
    for (const handler of listeners.get(type) ?? []) handler();
  };
  return { host, fire, listeners };
}

describe('platformFromBackend', () => {
  it.each([
    ['edgechromium', 'windows'],
    ['mshtml', 'windows'],
    ['cef', 'windows'],
    ['cocoa', 'macos'],
    ['gtkwebkit2', 'other'],
    ['qtwebengine', 'other'],
  ])('maps the %s backend onto its operating system', (backend, expected) => {
    expect(platformFromBackend(backend, '')).toBe(expected);
  });

  // A launcher newer than this bundle can name a backend it has never seen, and
  // the placement of the buttons must still be right rather than defaulting.
  it('falls back to the user agent for an unknown backend', () => {
    expect(platformFromBackend('quantumview', 'Mozilla/5.0 (Macintosh; Intel Mac OS X)')).toBe('macos');
    expect(platformFromBackend(undefined, 'Mozilla/5.0 (Windows NT 10.0; Win64)')).toBe('windows');
    expect(platformFromBackend('', 'Mozilla/5.0 (X11; Linux x86_64)')).toBe('other');
  });
});

describe('NativeWindowStore', () => {
  it('reports nothing at all in a browser', async () => {
    const { host } = fakeHost();
    const store = new NativeWindowStore({ host });
    store.subscribe(() => undefined);
    await store.refresh();
    expect(store.getSnapshot()).toEqual({
      present: false,
      customFrame: false,
      maximized: false,
      platform: 'other',
      topInset: 0,
    });
  });

  // The whole point of reporting `customFrame` rather than inferring it: an
  // older launcher is a native window that still has its own OS title bar, and
  // drawing a second set of buttons over it would be worse than drawing none.
  it('treats a launcher with no window API as native but framed', async () => {
    const { host } = fakeHost({ bridge: { platform: 'edgechromium', api: {} } });
    const store = new NativeWindowStore({ host });
    store.subscribe(() => undefined);
    await store.refresh();
    expect(store.getSnapshot().present).toBe(true);
    expect(store.getSnapshot().customFrame).toBe(false);
  });

  it('adopts the state a custom-frame launcher reports', async () => {
    const { host } = fakeHost({
      bridge: {
        platform: 'edgechromium',
        api: { window_state: async () => ({ maximized: true, customFrame: true }) },
      },
    });
    const store = new NativeWindowStore({ host });
    store.subscribe(() => undefined);
    await store.refresh();
    expect(store.getSnapshot()).toEqual({
      present: true,
      customFrame: true,
      maximized: true,
      platform: 'windows',
      // Windows never reserves anything: the menu bar is not an overlay there.
      topInset: 0,
    });
  });

  it('keeps the OS controls in charge when the bridge throws', async () => {
    const { host } = fakeHost({
      bridge: {
        platform: 'edgechromium',
        api: { window_state: async () => { throw new Error('bridge is gone'); } },
      },
    });
    const store = new NativeWindowStore({ host });
    store.subscribe(() => undefined);
    await store.refresh();
    expect(store.getSnapshot().present).toBe(true);
    expect(store.getSnapshot().customFrame).toBe(false);
  });

  it('notifies subscribers only when something actually changed', async () => {
    const { host } = fakeHost({
      bridge: {
        platform: 'edgechromium',
        api: { window_state: async () => ({ maximized: false, customFrame: true }) },
      },
    });
    const store = new NativeWindowStore({ host });
    const listener = vi.fn();
    store.subscribe(listener);
    await store.refresh();
    await store.refresh();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('forwards each button to its bridge call', async () => {
    const window_minimize = vi.fn(async () => undefined);
    const window_close = vi.fn(async () => undefined);
    const { host } = fakeHost({
      bridge: { platform: 'edgechromium', api: { window_minimize, window_close } },
    });
    const store = new NativeWindowStore({ host });
    await store.minimize();
    await store.close();
    expect(window_minimize).toHaveBeenCalledOnce();
    expect(window_close).toHaveBeenCalledOnce();
  });

  // The glyph has to flip on the click, not on the answer -- a maximize button
  // that waits for a round trip reads as a dropped click.
  it('flips the maximize glyph before the bridge answers', async () => {
    let resolveToggle: (value: { maximized: boolean }) => void = () => undefined;
    const { host } = fakeHost({
      bridge: {
        platform: 'edgechromium',
        api: {
          window_state: async () => ({ maximized: false, customFrame: true }),
          window_toggle_maximize: () => new Promise<{ maximized: boolean }>((resolve) => {
            resolveToggle = resolve;
          }),
        },
      },
    });
    const store = new NativeWindowStore({ host });
    store.subscribe(() => undefined);
    await store.refresh();
    expect(store.getSnapshot().maximized).toBe(false);

    const pending = store.toggleMaximize();
    expect(store.getSnapshot().maximized).toBe(true);
    resolveToggle({ maximized: true });
    await pending;
    expect(store.getSnapshot().maximized).toBe(true);
  });

  // pywebview has no maximize event, so a snap or a Win+Arrow is only ever
  // observed as a DOM resize. Missing it leaves the glyph lying about the window.
  it('re-reads the window after a resize settles', async () => {
    // Subscribing already arms the bridge, so the answer is a mutable fact
    // rather than a call-ordered mock: the test then says what the window is,
    // not how many times it was asked.
    let maximized = false;
    const { host, fire } = fakeHost({
      bridge: {
        platform: 'edgechromium',
        api: { window_state: async () => ({ maximized, customFrame: true }) },
      },
    });
    const store = new NativeWindowStore({ host, settleMs: 0 });
    store.subscribe(() => undefined);
    await store.refresh();
    expect(store.getSnapshot().maximized).toBe(false);

    maximized = true;
    fire('resize');
    await new Promise((resolve) => setTimeout(resolve, 1));
    expect(store.getSnapshot().maximized).toBe(true);
  });

  it('ignores resize entirely in a browser, where there is no bridge to ask', async () => {
    const { host, fire } = fakeHost();
    const store = new NativeWindowStore({ host, settleMs: 0 });
    store.subscribe(() => undefined);
    await store.refresh();
    fire('resize');
    await new Promise((resolve) => setTimeout(resolve, 1));
    expect(store.getSnapshot().present).toBe(false);
  });

  it('picks the bridge up whether the object or the event arrives first', async () => {
    const { host, fire } = fakeHost();
    const store = new NativeWindowStore({ host });
    store.subscribe(() => undefined);
    expect(store.getSnapshot().present).toBe(false);

    (host as { pywebview?: unknown }).pywebview = {
      platform: 'cocoa',
      api: { window_state: async () => ({ maximized: false, customFrame: true }) },
    };
    fire('pywebviewready');
    await new Promise((resolve) => setTimeout(resolve, 1));
    expect(store.getSnapshot()).toMatchObject({ present: true, customFrame: true, platform: 'macos' });
  });
});
