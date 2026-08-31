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
    return () => {
      delete root.dataset.nativeFrame;
      delete root.dataset.nativeMaximized;
    };
  }, [owns, state.maximized, state.platform]);

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

  const maximizeLabel = state.maximized ? 'Restore down' : 'Maximize';
  return <div className={`window-controls window-controls-${state.platform}`} role="group" aria-label="Window">
    {state.platform === 'macos' && <button
      type="button"
      className="window-controls-close"
      title="Close"
      aria-label="Close"
      onClick={() => { void store.close(); }}
    ><CloseGlyph/></button>}
    <button
      type="button"
      className="window-controls-minimize"
      title="Minimize"
      aria-label="Minimize"
      onClick={() => { void store.minimize(); }}
    ><MinimizeGlyph/></button>
    <button
      type="button"
      className="window-controls-maximize"
      title={maximizeLabel}
      aria-label={maximizeLabel}
      onClick={() => { void store.toggleMaximize(); }}
    >{state.maximized ? <RestoreGlyph/> : <MaximizeGlyph/>}</button>
    {state.platform !== 'macos' && <button
      type="button"
      className="window-controls-close"
      title="Close"
      aria-label="Close"
      onClick={() => { void store.close(); }}
    ><CloseGlyph/></button>}
  </div>;
}
