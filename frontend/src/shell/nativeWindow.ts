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
  /**
   * Pixels the interface must leave clear at the top of the window.
   *
   * Non-zero only in macOS full screen, where the menu bar slides down over
   * whatever is beneath it and the window's own layout metrics report nothing:
   * `contentLayoutRect` and `safeAreaInsets.top` are both 0 there. Without the
   * reservation the top bar's brand, menus and Solve button sit underneath it.
   */
  topInset: number;
}

/** What the launcher exposes as `js_api`. Each call is asynchronous. */
interface NativeWindowApi {
  window_state?: () => Promise<{
    maximized?: boolean;
    customFrame?: boolean;
    topInset?: number;
  }>;
  window_minimize?: () => Promise<{ maximized?: boolean } | void>;
  window_toggle_maximize?: (zoom?: boolean) => Promise<{ maximized?: boolean } | void>;
  window_close?: () => Promise<void>;
  window_set_title?: (title: string) => Promise<void>;
  window_begin_drag?: () => Promise<void>;
  window_double_click?: () => Promise<{ action?: string } | void>;
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
  topInset: 0,
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
        topInset: Number.isFinite(state?.topInset) ? Number(state?.topInset) : 0,
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
      current.platform === next.platform &&
      current.topInset === next.topInset
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

  /**
   * Take the window to full screen, or zoom it when `zoom` is set.
   *
   * The two are one button because that is how macOS's green one works: a plain
   * click is full screen, Option-click is zoom. Windows has no such split and
   * ignores the argument -- there the button maximizes either way.
   */
  async toggleMaximize(zoom = false): Promise<void> {
    const api = this.api();
    if (!api?.window_toggle_maximize) return;
    if (!zoom) this.assume(!this.snapshot.maximized);
    const result = await api.window_toggle_maximize(zoom);
    if (result && typeof result.maximized === 'boolean') this.assume(result.maximized);
    else await this.refresh();
  }

  async close(): Promise<void> {
    await this.api()?.window_close?.();
  }

  /**
   * Ask the host to take over the pointer and move the window.
   *
   * macOS only, and not an optimization over a JavaScript drag -- it is the only
   * drag available. WebKit does not implement `-webkit-app-region` at all
   * (`CSS.supports('-webkit-app-region', 'drag')` is false in this WKWebView),
   * so the stylesheet's drag region reaches Windows and nothing else. AppKit's
   * `performWindowDragWithEvent:` runs the gesture from the click still in
   * flight, which is what makes moving between Spaces and displays -- and the
   * snapping that comes with them -- behave like every other window's.
   *
   * On Windows this is never called: the runtime has already handed the top bar
   * to the OS as caption before a mousedown reaches the document.
   */
  async beginDrag(): Promise<void> {
    await this.api()?.window_begin_drag?.();
  }

  /**
   * Answer a double-click on the top bar.
   *
   * The host decides what that means, because macOS lets the user choose
   * between zoom, minimize and nothing in System Settings, and with the title
   * bar gone this bar is the only surface left that can honour the choice.
   */
  async doubleClick(): Promise<void> {
    await this.api()?.window_double_click?.();
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
