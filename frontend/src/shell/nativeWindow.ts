/**
 * The one place that knows WG is running in a native window rather than a tab.
 *
 * The shipped app hosts this SPA in a pywebview/WebView2 window, and that window
 * has no tab strip, no address bar and -- once the launcher installs its custom
 * frame -- no OS title bar either. The buttons that replace it have to live in
 * the interface, which means the interface needs three facts the browser never
 * had to supply: whether there is a native window at all, whether that window's
 * caption has actually been removed, and whether it is currently maximized.
 *
 * All three come from the launcher, never from feature detection. `customFrame`
 * in particular is reported rather than assumed: the native side removes the
 * caption only where it can do so without also losing resize, Aero Snap and the
 * maximize animation, so a build that has not earned the custom controls says so
 * and keeps the OS title bar. Guessing here would put two sets of window buttons
 * on one window, or none.
 *
 * In a browser every export below is inert: `present` stays false, the component
 * renders nothing, and no bridge call is ever made.
 */

/** The operating system, as the placement of window controls understands it. */
export type HostPlatform = 'windows' | 'macos' | 'other';

export interface NativeWindowSnapshot {
  /** A native launcher answered. False in a dev browser. */
  present: boolean;
  /** The launcher removed the OS caption, so the app owes the user controls. */
  customFrame: boolean;
  maximized: boolean;
  platform: HostPlatform;
}

/** What the launcher exposes as `js_api`. Each call is asynchronous. */
interface NativeWindowApi {
  window_state?: () => Promise<{ maximized?: boolean; customFrame?: boolean }>;
  window_minimize?: () => Promise<{ maximized?: boolean } | void>;
  window_toggle_maximize?: () => Promise<{ maximized?: boolean } | void>;
  window_close?: () => Promise<void>;
  window_set_title?: (title: string) => Promise<void>;
}

interface PywebviewBridge {
  /** pywebview's own backend name: `edgechromium`, `cocoa`, `gtkwebkit2`, … */
  platform?: string;
  api?: NativeWindowApi;
}

declare global {
  interface Window {
    pywebview?: PywebviewBridge;
  }
}

const ABSENT: NativeWindowSnapshot = {
  present: false,
  customFrame: false,
  maximized: false,
  platform: 'other',
};

/**
 * Map pywebview's *backend* name onto the operating system.
 *
 * The backend is the more reliable signal of the two available here: it is the
 * toolkit the launcher actually chose, whereas WebView2 reports a Chrome-shaped
 * user agent that says nothing about which shell is hosting it. `navigator` is
 * kept only for the case where the bridge exists but names a backend this
 * version has not seen.
 */
export function platformFromBackend(backend: string | undefined, signal: string): HostPlatform {
  switch ((backend ?? '').toLowerCase()) {
    case 'edgechromium':
    case 'mshtml':
    case 'cef':
    case 'winforms':
      return 'windows';
    case 'cocoa':
      return 'macos';
    case 'gtkwebkit2':
    case 'qtwebengine':
    case 'qtwebkit':
      return 'other';
    default:
      if (/mac|iphone|ipad|ipod/i.test(signal)) return 'macos';
      if (/win/i.test(signal)) return 'windows';
      return 'other';
  }
}

type Listener = () => void;

export interface NativeWindowStoreOptions {
  /** Injected in tests; defaults to the real `window`. */
  host?: Window & typeof globalThis;
  /** Injected in tests so no timer is needed to observe a resize. */
  settleMs?: number;
}

/**
 * Tracks the native window and forwards the three button actions to it.
 *
 * Maximized state is *pulled*, not pushed. pywebview has no maximize event to
 * subscribe to, but the DOM `resize` event fires for every maximize, restore and
 * snap, so one debounced query per settled resize keeps the glyph correct
 * without a Python-side notification channel. The debounce matters: a live
 * resize drag would otherwise make one bridge round trip per animation frame.
 */
export class NativeWindowStore {
  private readonly host: Window & typeof globalThis;
  private readonly settleMs: number;
  private readonly listeners = new Set<Listener>();
  private snapshot: NativeWindowSnapshot = ABSENT;
  private settleTimer: ReturnType<typeof setTimeout> | undefined;
  private started = false;

  constructor({ host, settleMs = 150 }: NativeWindowStoreOptions = {}) {
    this.host = host ?? (globalThis as Window & typeof globalThis);
    this.settleMs = settleMs;
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    this.start();
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): NativeWindowSnapshot => this.snapshot;

  /**
   * Begin listening for the bridge.
   *
   * pywebview injects `window.pywebview` and then fires `pywebviewready`, and
   * which of the two comes first depends on how long the bundle took to
   * evaluate. Both are handled, and the event listener stays registered even
   * when the object is already present, because a reload re-injects it.
   */
  start(): void {
    if (this.started) return;
    this.started = true;
    if (typeof this.host.addEventListener !== 'function') return;
    this.host.addEventListener('pywebviewready', this.onReady);
    this.host.addEventListener('resize', this.onResize);
    if (this.host.pywebview) this.onReady();
  }

  stop(): void {
    if (!this.started) return;
    this.started = false;
    this.host.removeEventListener?.('pywebviewready', this.onReady);
    this.host.removeEventListener?.('resize', this.onResize);
    if (this.settleTimer !== undefined) clearTimeout(this.settleTimer);
  }

  private readonly onReady = (): void => {
    void this.refresh();
  };

  private readonly onResize = (): void => {
    if (!this.snapshot.present) return;
    if (this.settleTimer !== undefined) clearTimeout(this.settleTimer);
    this.settleTimer = setTimeout(() => {
      this.settleTimer = undefined;
      void this.refresh();
    }, this.settleMs);
  };

  private api(): NativeWindowApi | undefined {
    return this.host.pywebview?.api;
  }

  /** Ask the launcher what it is, and what state its window is in. */
  async refresh(): Promise<void> {
    const api = this.api();
    if (!api?.window_state) {
      // A bridge with no window API is an older launcher: it is a native window,
      // but not one that has given up its caption, so the OS controls remain.
      this.publish(this.host.pywebview ? { ...ABSENT, present: true } : ABSENT);
      return;
    }
    try {
      const state = await api.window_state();
      this.publish({
        present: true,
        customFrame: state?.customFrame === true,
        maximized: state?.maximized === true,
        platform: platformFromBackend(
          this.host.pywebview?.platform,
          this.host.navigator?.userAgent ?? '',
        ),
      });
    } catch {
      // The bridge answering badly is not a reason to lose the window: fall back
      // to "native, but no custom controls", which leaves the OS caption in
      // charge rather than leaving the window with no way to be closed.
      this.publish({ ...ABSENT, present: true });
    }
  }

  private publish(next: NativeWindowSnapshot): void {
    const current = this.snapshot;
    if (
      current.present === next.present &&
      current.customFrame === next.customFrame &&
      current.maximized === next.maximized &&
      current.platform === next.platform
    ) {
      return;
    }
    this.snapshot = next;
    for (const listener of this.listeners) listener();
  }

  /**
   * Apply an optimistic maximize/restore before the bridge answers.
   *
   * The round trip is short but not instant, and a maximize button whose glyph
   * lags the window it just moved reads as a dropped click.
   */
  private assume(maximized: boolean): void {
    this.publish({ ...this.snapshot, maximized });
  }

  async minimize(): Promise<void> {
    await this.api()?.window_minimize?.();
  }

  async toggleMaximize(): Promise<void> {
    const api = this.api();
    if (!api?.window_toggle_maximize) return;
    this.assume(!this.snapshot.maximized);
    const result = await api.window_toggle_maximize();
    if (result && typeof result.maximized === 'boolean') this.assume(result.maximized);
    else await this.refresh();
  }

  async close(): Promise<void> {
    await this.api()?.window_close?.();
  }

  /**
   * Name the window in the taskbar and Alt-Tab.
   *
   * With the caption gone this is the only place the current design name can
   * still appear, and pywebview does not propagate `document.title` to the
   * native window on its own.
   */
  async setTitle(title: string): Promise<void> {
    await this.api()?.window_set_title?.(title);
  }
}

export const nativeWindow = new NativeWindowStore();
