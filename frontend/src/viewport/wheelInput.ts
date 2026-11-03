import { wheelDeviceEvidence, type ScrollDevice } from '../input/wheelDevice';
import type { ViewerPreferences } from '../viewerprefs/viewerPreferences';

const STREAM_IDLE_MS = 400;
const SAMPLE_WAIT_MS = 40;

/** One capture listener owns device classification, panning and zoom inversion.
 * Ambiguous starts wait at most 40 ms for samples that break a common notch
 * step. Acceleration changes the multiple of that step, not the device.
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
  let pending: WheelEvent[] = [];
  let notchStep = 0;
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
    const buffered = pending;
    pending = [];
    for (const event of buffered) route(event, source, true);
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
      if (pending.length) flush('wheel');
      device = null;
    }
    if (!device) {
      if (pending.length) {
        stop(event);
        pending.push(event);
        const y = Math.abs(event.deltaY);
        // Check the entire probe against one fixed step, even if a later
        // sample alone looks like a wheel. WebKit acceleration is 40/80/120,
        // whereas precise input can leave that grid at any point in the probe.
        if (event.deltaMode !== 0 || event.deltaX !== 0 || !Number.isInteger(y)
          || y < notchStep || y % notchStep !== 0) flush('trackpad');
        return;
      } else {
        const evidence = wheelDeviceEvidence(event);
        if (evidence) {
          device = evidence;
        } else {
          stop(event);
          pending.push(event);
          // Use WebKit's base step even when the first notch is accelerated.
          // Other ambiguous integer starts retain their initial step.
          notchStep = event.deltaY % 40 === 0 ? 40 : Math.abs(event.deltaY);
          timer = setTimeout(() => flush('wheel'), SAMPLE_WAIT_MS);
          return;
        }
      }
    }
    route(event, device!);
  };
  element.addEventListener('wheel', onWheel, { capture: true, passive: false });
  return () => {
    clearTimeout(timer);
    pending = [];
    element.removeEventListener('wheel', onWheel, { capture: true });
  };
}
