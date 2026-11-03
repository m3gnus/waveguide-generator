import { wheelDeviceEvidence, type ScrollDevice } from '../input/wheelDevice';
import type { ViewerPreferences } from '../viewerprefs/viewerPreferences';

const STREAM_IDLE_MS = 400;
const SAMPLE_WAIT_MS = 40;

/** One capture listener owns device classification, panning and zoom inversion.
 * Ambiguous starts wait at most 40 ms for a rapid, varying sample (Mapbox's
 * delayed-start approach; VS Code also uses non-accelerating notch evidence).
 * Once chosen, the device stays latched through momentum until 400 ms idle.
 * Zooms go to the existing OrbitControls unchanged, including cursor position,
 * delta units, event count and ctrlKey, preserving its zoom speed and direction.
 */
export function installViewportWheelInput(
  element: HTMLElement,
  { scrollPans, invertWheelZoom }: Pick<ViewerPreferences, 'scrollPans' | 'invertWheelZoom'>,
  pan: (deltaX: number, deltaY: number) => void,
): () => void {
  if (!scrollPans && !invertWheelZoom) return () => undefined;
  const redispatched = new WeakSet<Event>();
  let device: ScrollDevice | null = null;
  let lastTime = -Infinity;
  let pending: WheelEvent | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const stop = (event: WheelEvent) => {
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  const route = (event: WheelEvent, source: ScrollDevice, delayed = false) => {
    if (scrollPans && source === 'trackpad' && !event.ctrlKey) {
      stop(event);
      pan(event.deltaX, event.deltaY);
    } else if (invertWheelZoom || delayed) {
      stop(event);
      const replacement = new WheelEvent('wheel', {
        deltaMode: event.deltaMode,
        deltaX: event.deltaX,
        deltaY: invertWheelZoom ? -event.deltaY : event.deltaY,
        deltaZ: event.deltaZ,
        ctrlKey: event.ctrlKey,
        shiftKey: event.shiftKey,
        altKey: event.altKey,
        metaKey: event.metaKey,
        clientX: event.clientX,
        clientY: event.clientY,
        screenX: event.screenX,
        screenY: event.screenY,
        bubbles: true,
        cancelable: true,
      });
      redispatched.add(replacement);
      element.dispatchEvent(replacement);
    }
  };
  const flush = (source: ScrollDevice) => {
    clearTimeout(timer);
    timer = undefined;
    device = source;
    const first = pending;
    pending = null;
    if (first) route(first, source, true);
  };
  const onWheel = (event: WheelEvent) => {
    if (redispatched.has(event)) return;
    // Pinch always zooms, without teaching the plain-scroll classifier anything.
    if (event.ctrlKey || !scrollPans) {
      route(event, 'wheel');
      return;
    }
    if (event.deltaX === 0 && event.deltaY === 0) return;
    const gap = event.timeStamp - lastTime;
    lastTime = event.timeStamp;
    if (gap >= STREAM_IDLE_MS) {
      if (pending) flush('wheel');
      device = null;
    }
    if (!device) {
      const evidence = wheelDeviceEvidence(event);
      if (pending) {
        const varying = Math.abs(event.deltaY) !== Math.abs(pending.deltaY);
        flush(evidence ?? (gap < SAMPLE_WAIT_MS && varying ? 'trackpad' : 'wheel'));
      } else if (evidence) {
        device = evidence;
      } else {
        stop(event);
        pending = event;
        timer = setTimeout(() => flush('wheel'), SAMPLE_WAIT_MS);
        return;
      }
    }
    route(event, device!);
  };
  element.addEventListener('wheel', onWheel, { capture: true, passive: false });
  return () => {
    clearTimeout(timer);
    pending = null;
    element.removeEventListener('wheel', onWheel, { capture: true });
  };
}
