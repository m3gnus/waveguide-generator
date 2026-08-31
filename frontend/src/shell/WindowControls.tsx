import { useEffect, useSyncExternalStore } from 'react';
import { nativeWindow, type HostPlatform, type NativeWindowStore } from './nativeWindow';
import '../styles/windowControls.css';

/**
 * The minimize / maximize / close buttons that stand in for the OS title bar.
 *
 * The shipped app removes the caption so the top bar can start at the very top
 * of the window, the way Claude, Spotify and VS Code do. That is only an
 * improvement if the window is still fully operable, so these three buttons are
 * not decoration: with the caption gone they are the *only* way to minimize,
 * maximize or close from the pointer.
 *
 * They therefore render on exactly one condition -- the launcher reporting that
 * it really did remove the caption. A browser tab, an older launcher, or a
 * platform where the native side declined the custom frame all render nothing
 * and keep the OS controls they already had. There is no arrangement in which
 * the window ends up with two sets of buttons, or none.
 *
 * Placement follows the host, not a house style. Windows puts them at the
 * trailing edge and macOS at the leading edge, and both are muscle memory deep
 * enough that the wrong corner reads as a bug. `side` lets the top bar mount one
 * instance at each end and lets this component pick which one is real.
 */

type Side = 'leading' | 'trailing';

/**
 * Everything in the top bar a pointer is meant to operate rather than drag by.
 *
 * `windowControls.css` opts the same list out of `app-region` for Windows, and
 * `WindowControls.test.tsx` pins the two lists to each other -- a control that
 * appears in one and not the other is either undraggable where it should drag or
 * unclickable where it should click, and neither is visible in review.
 */
export const NO_DRAG_SELECTOR = [
  'button', 'a', 'input', 'select', 'textarea', 'kbd', 'dialog',
  '[role=\'button\']', '[role=\'radiogroup\']', '[role=\'menu\']',
  '[role=\'menuitem\']', '[role=\'dialog\']',
  '.design-menu-popover', '.command-affordance',
].join(', ');

/** Which end of the title bar each platform hangs its controls from. */
const SIDE_BY_PLATFORM: Record<HostPlatform, Side | null> = {
  windows: 'trailing',
  macos: 'leading',
  other: null,
};

function MinimizeGlyph() {
  return <svg viewBox="0 0 10 10" aria-hidden="true" shapeRendering="crispEdges">
    <path d="M0 5h10" stroke="currentColor" strokeWidth="1" fill="none"/>
  </svg>;
}

function MaximizeGlyph() {
  return <svg viewBox="0 0 10 10" aria-hidden="true" shapeRendering="crispEdges">
    <rect x="0.5" y="0.5" width="9" height="9" stroke="currentColor" strokeWidth="1" fill="none"/>
  </svg>;
}

/** Two offset outlines -- the platform's own way of saying "shrink back". */
function RestoreGlyph() {
  return <svg viewBox="0 0 10 10" aria-hidden="true" shapeRendering="crispEdges">
    <path d="M2.5 2.5V0.5h7v7h-2" stroke="currentColor" strokeWidth="1" fill="none"/>
    <rect x="0.5" y="2.5" width="7" height="7" stroke="currentColor" strokeWidth="1" fill="none"/>
  </svg>;
}

function CloseGlyph() {
  return <svg viewBox="0 0 10 10" aria-hidden="true">
    <path d="M0.5 0.5l9 9M9.5 0.5l-9 9" stroke="currentColor" strokeWidth="1.1" fill="none"/>
  </svg>;
}

/* macOS glyphs -------------------------------------------------------------
 *
 * The Windows set above is drawn for a 46x32 caption button on a flat
 * background. Reusing it inside a 12 px traffic light is what made the dots
 * read as a Windows title bar wearing macOS colours -- a square outline for
 * maximize above all, which is not a shape the platform draws anywhere.
 *
 * These are traced from a measurement of the real buttons rather than eyeballed
 * (lwouis/macos-traffic-light-buttons-as-SVG, whose art board is 85.4 units to
 * the 12 px button; every number below is that source scaled by 12/85.4).
 * Getting them close is not enough: these sit two pixels from a real title bar
 * on the same screen, so a glyph a pixel wide or a pixel long reads as wrong
 * without the viewer being able to say why. In particular the cross is 5.7 px
 * across, not the 3.5 px a first guess put there, and the minus is 8 px -- two
 * thirds of the button, far wider than it looks like it should be.
 *
 * The green button carries the full-screen chevrons, pointing out of the corners
 * it will expand into and back in toward the centre once it is full screen.
 * That glyph is a promise about what the button does, which is why the button
 * enters full screen rather than zooming: Apple's plain click is full screen and
 * its Option-click is zoom, and `WindowControls` follows both.
 */

/** The centre-line of a 0.98 px bar, round-capped, in the button's 12 px box. */
function MacCloseGlyph() {
  return <svg viewBox="0 0 12 12" aria-hidden="true">
    <path d="M3.65 3.65 8.35 8.35M8.35 3.65 3.65 8.35"
      stroke="currentColor" strokeWidth="0.98" strokeLinecap="round" fill="none"/>
  </svg>;
}

function MacMinimizeGlyph() {
  return <svg viewBox="0 0 12 12" aria-hidden="true">
    <path d="M2.50 6h7.01"
      stroke="currentColor" strokeWidth="0.98" strokeLinecap="round" fill="none"/>
  </svg>;
}

/** Chevrons in the top-right and bottom-left, pointing out of the window. */
function MacFullscreenGlyph() {
  return <svg viewBox="0 0 12 12" aria-hidden="true">
    <path d="M4.38 2.92h3.76a.91.91 0 0 1 .91.91v3.76z" fill="currentColor"/>
    <path d="M7.64 9.06H3.88a.91.91 0 0 1-.91-.91V4.38z" fill="currentColor"/>
  </svg>;
}

/** The same two, turned to face the centre: this gives the screen back. */
function MacRestoreScreenGlyph() {
  return <svg viewBox="0 0 12 12" aria-hidden="true">
    <path d="M9.05 7.6V3.83a.91.91 0 0 0-.91-.91H4.38z" fill="currentColor"/>
    <path d="M2.97 4.38v3.77a.91.91 0 0 0 .91.91h3.76z" fill="currentColor"/>
  </svg>;
}

export interface WindowControlsProps {
  side: Side;
  /** Injected in tests so no pywebview bridge is needed. */
  store?: NativeWindowStore;
}

export function WindowControls({ side, store = nativeWindow }: WindowControlsProps) {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const active = state.customFrame ? SIDE_BY_PLATFORM[state.platform] : null;
  // The top bar mounts one instance at each end, and exactly one of them is
  // real. Only that one touches the document, so the two can never race over a
  // single attribute or tear it down from under each other.
  const owns = active === side;

  // The document element carries the frame state because the rules that depend
  // on it are not this component's own: the drag region belongs to the top bar,
  // and the maximized padding to the whole chrome. One attribute keeps that in
  // CSS instead of threading a prop through every element that cares.
  //
  // The cleanup is load-bearing rather than tidiness. `data-native-frame` is
  // what turns the top bar into a drag region, and a drag region swallows the
  // clicks underneath it -- so an attribute outliving the controls would leave
  // the whole bar looking normal and responding to nothing.
  useEffect(() => {
    if (!owns) return undefined;
    const root = document.documentElement;
    root.dataset.nativeFrame = state.platform;
    root.dataset.nativeMaximized = String(state.maximized);
    root.style.setProperty('--native-top-inset', `${state.topInset}px`);
    return () => {
      delete root.dataset.nativeFrame;
      delete root.dataset.nativeMaximized;
      root.style.removeProperty('--native-top-inset');
    };
  }, [owns, state.maximized, state.platform, state.topInset]);

  // macOS has to ask for its drags; Windows is given them.
  //
  // WebKit does not implement `-webkit-app-region`, so on macOS the stylesheet's
  // drag region does nothing and the gesture has to start here: a plain
  // mousedown in the bar, on something that is not a control, hands the click to
  // AppKit. The listener is a capturing one so that it sees the event before any
  // component can stop it, and the second click of a double-click is answered as
  // a double-click instead -- otherwise the gesture would begin a second
  // zero-distance drag and the window would never zoom.
  useEffect(() => {
    if (!owns || state.platform !== 'macos') return undefined;
    const onMouseDown = (event: MouseEvent) => {
      if (event.button !== 0 || event.defaultPrevented) return;
      const target = event.target as Element | null;
      if (typeof target?.closest !== 'function') return;
      if (!target.closest('.topbar') || target.closest(NO_DRAG_SELECTOR)) return;
      if (event.detail >= 2) void store.doubleClick();
      else void store.beginDrag();
    };
    document.addEventListener('mousedown', onMouseDown, true);
    return () => { document.removeEventListener('mousedown', onMouseDown, true); };
  }, [owns, state.platform, store]);

  // Which window has focus was the title bar's job. Nothing else in the
  // interface says it, so the controls dim instead.
  useEffect(() => {
    if (!owns) return undefined;
    const root = document.documentElement;
    const sync = () => { root.dataset.nativeBlurred = String(!document.hasFocus()); };
    sync();
    window.addEventListener('focus', sync);
    window.addEventListener('blur', sync);
    return () => {
      window.removeEventListener('focus', sync);
      window.removeEventListener('blur', sync);
      delete root.dataset.nativeBlurred;
    };
  }, [owns]);

  if (active !== side) return null;

  const mac = state.platform === 'macos';
  const maximizeLabel = state.maximized
    ? (mac ? 'Exit full screen' : 'Restore down')
    : (mac ? 'Enter full screen' : 'Maximize');
  const close = mac ? <MacCloseGlyph/> : <CloseGlyph/>;
  const minimize = mac ? <MacMinimizeGlyph/> : <MinimizeGlyph/>;
  const maximize = state.maximized
    ? (mac ? <MacRestoreScreenGlyph/> : <RestoreGlyph/>)
    : (mac ? <MacFullscreenGlyph/> : <MaximizeGlyph/>);
  return <div className={`window-controls window-controls-${state.platform}`} role="group" aria-label="Window">
    {mac && <button
      type="button"
      className="window-controls-close"
      title="Close"
      aria-label="Close"
      onClick={() => { void store.close(); }}
    >{close}</button>}
    <button
      type="button"
      className="window-controls-minimize"
      title="Minimize"
      aria-label="Minimize"
      onClick={() => { void store.minimize(); }}
    >{minimize}</button>
    <button
      type="button"
      className="window-controls-maximize"
      title={mac ? `${maximizeLabel} (hold Option to zoom)` : maximizeLabel}
      aria-label={maximizeLabel}
      onClick={(event) => { void store.toggleMaximize(mac && event.altKey); }}
    >{maximize}</button>
    {!mac && <button
      type="button"
      className="window-controls-close"
      title="Close"
      aria-label="Close"
      onClick={() => { void store.close(); }}
    >{close}</button>}
  </div>;
}
