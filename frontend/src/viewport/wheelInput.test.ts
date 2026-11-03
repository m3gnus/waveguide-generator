import { OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';
import { OrbitControls } from 'three-stdlib';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { installViewportWheelInput } from './wheelInput';

// Recorded-shape fixtures, reconstructed from WebKit's native-event conversion
// and established browser wheel traces; these are not a hardware capture from
// the desktop shell. Legacy fields are readonly and absent from WheelEventInit.
type Sample = WheelEventInit & { wheelDeltaY?: number; wheelDelta?: number };
function wheel(sample: Sample, timeStamp = 0): WheelEvent {
  const { wheelDeltaY, wheelDelta, ...init } = sample;
  const event = new WheelEvent('wheel', { cancelable: true, ...init });
  Object.defineProperty(event, 'timeStamp', { value: timeStamp });
  if (wheelDeltaY !== undefined) Object.defineProperty(event, 'wheelDeltaY', { value: wheelDeltaY });
  if (wheelDelta !== undefined) Object.defineProperty(event, 'wheelDelta', { value: wheelDelta });
  return event;
}

const cleanups: (() => void)[] = [];
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup());
  vi.useRealTimers();
});
function harness(scrollPans = true, invertWheelZoom = false) {
  const element = document.createElement('div');
  const pan = vi.fn();
  const zoom = vi.fn<(event: WheelEvent) => void>();
  // OrbitControls listens in the bubble phase; registration order must not matter.
  element.addEventListener('wheel', zoom);
  const detach = installViewportWheelInput(element, { scrollPans, invertWheelZoom }, pan);
  cleanups.push(detach);
  return { element, pan, zoom, detach, send: (sample: Sample, time = 0) => {
    const event = wheel(sample, time);
    element.dispatchEvent(event);
    return event;
  } };
}

const notches: [string, Sample][] = [
  ['Safari pixel notch', { deltaY: 40, wheelDeltaY: -120, wheelDelta: -120 }],
  ['small smooth mouse notch', { deltaY: 4, wheelDeltaY: -120 }],
  ['fractional smooth mouse notch', { deltaY: 1.5, wheelDeltaY: -120 }],
  ['Safari accelerated notch', { deltaY: 80, wheelDeltaY: -240 }],
  ['legacy wheelDelta fallback', { deltaY: 40, wheelDelta: -120 }],
  ['Chromium pixel notch', { deltaY: 100 }],
  ['120 pixel notch', { deltaY: 120 }],
  ['historical macOS fractional notch', { deltaY: 4.000244140625 }],
  ['Firefox line notch', { deltaY: 3, deltaMode: WheelEvent.DOM_DELTA_LINE }],
  ['page notch', { deltaY: 1, deltaMode: WheelEvent.DOM_DELTA_PAGE }],
];

describe('viewport wheel gestures', () => {
  it.each(notches)('%s zooms in both directions while two-finger pan is enabled', (_, sample) => {
    const h = harness();
    const down = h.send(sample);
    const up = h.send({
      ...sample, deltaY: -sample.deltaY!,
      wheelDeltaY: sample.wheelDeltaY === undefined ? undefined : -sample.wheelDeltaY,
      wheelDelta: sample.wheelDelta === undefined ? undefined : -sample.wheelDelta,
    }, 80);
    expect(h.pan).not.toHaveBeenCalled();
    expect(h.zoom.mock.calls.map(([event]) => event.deltaY)).toEqual([sample.deltaY, -sample.deltaY!]);
    expect(h.zoom.mock.calls.map(([event]) => event)).toEqual([down, up]);
  });

  it.each([
    ['diagonal', [{ deltaX: 3, deltaY: -5 }, { deltaX: 2, deltaY: -10 }]],
    ['vertical integer Safari', [{ deltaY: -2, wheelDeltaY: 6 }, { deltaY: -12, wheelDeltaY: 36 }]],
    ['fractional', [{ deltaY: 0.75 }, { deltaY: 2.125 }]],
    ['fast fractional', [{ deltaY: 65.5, wheelDeltaY: -196 }, { deltaY: 92.25 }]],
    ['horizontal', [{ deltaX: 12 }, { deltaX: 5 }]],
  ] satisfies [string, Sample[]][])('%s trackpad stream pans', (_, samples) => {
    const h = harness();
    samples.forEach((sample, i) => expect(h.send(sample, i * 8).defaultPrevented).toBe(true));
    expect(h.pan.mock.calls).toEqual(samples.map((sample: Sample) => [sample.deltaX ?? 0, sample.deltaY ?? 0]));
    expect(h.zoom).not.toHaveBeenCalled();
  });

  it('keeps a trackpad gesture panning when momentum looks like notches', () => {
    const h = harness();
    [{ deltaY: 2 }, { deltaY: 40, wheelDeltaY: -120 }, { deltaY: 100 }, { deltaY: 0.5 }]
      .forEach((sample, i) => h.send(sample, i * 16));
    expect(h.pan).toHaveBeenCalledTimes(4);
    expect(h.zoom).not.toHaveBeenCalled();
  });

  it('keeps a wheel gesture zooming when later samples look like a trackpad', () => {
    const h = harness();
    h.send({ deltaY: 40, wheelDeltaY: -120 });
    h.send({ deltaX: 0.5, deltaY: 7.25 }, 16);
    h.send({ deltaY: 1 }, 32);
    expect(h.pan).not.toHaveBeenCalled();
    expect(h.zoom).toHaveBeenCalledTimes(3);
  });

  it('changes device only after the stream has been idle for 400 ms', () => {
    const h = harness();
    h.send({ deltaY: 2 });
    h.send({ deltaY: 100 }, 399);
    h.send({ deltaY: 100 }, 799);
    h.send({ deltaY: 2 }, 1199);
    expect(h.pan.mock.calls).toEqual([[0, 2], [0, 100], [0, 2]]);
    expect(h.zoom).toHaveBeenCalledTimes(1);
  });

  it('zooms a pinch during a pan without changing the plain-scroll stream', () => {
    const h = harness();
    h.send({ deltaY: 2 });
    const pinch = h.send({ deltaY: -1.5, ctrlKey: true, clientX: 72, clientY: 31 }, 8);
    h.send({ deltaY: 40, wheelDeltaY: -120 }, 16);
    expect(h.pan.mock.calls).toEqual([[0, 2], [0, 40]]);
    expect(h.zoom).toHaveBeenCalledExactlyOnceWith(pinch);
  });

  it('does not let a pinch seed the next plain-scroll device decision', () => {
    const h = harness();
    h.send({ deltaY: 1, ctrlKey: true });
    h.send({ deltaY: 40, wheelDeltaY: -120 }, 8);
    expect(h.pan).not.toHaveBeenCalled();
    expect(h.zoom).toHaveBeenCalledTimes(2);
  });

  it('buffers an ambiguous start and pans both samples of a rapid varying stream', () => {
    vi.useFakeTimers();
    const h = harness();
    h.send({ deltaY: 51 });
    expect(h.pan).not.toHaveBeenCalled();
    expect(h.zoom).not.toHaveBeenCalled();
    vi.advanceTimersByTime(8);
    h.send({ deltaY: 63 }, 8);
    expect(h.pan.mock.calls).toEqual([[0, 51], [0, 63]]);
    vi.runAllTimers();
    expect(h.zoom).not.toHaveBeenCalled();
  });

  it('treats repeated ambiguous magnitudes as notches and preserves both zoom events', () => {
    vi.useFakeTimers();
    const h = harness();
    h.send({ deltaY: 51 });
    h.send({ deltaY: -51 }, 20);
    vi.runAllTimers();
    expect(h.pan).not.toHaveBeenCalled();
    expect(h.zoom.mock.calls.map(([event]) => event.deltaY)).toEqual([51, -51]);
  });

  it('zooms an isolated ambiguous notch after at most 40 ms with its cursor and modifiers', () => {
    vi.useFakeTimers();
    const h = harness();
    h.send({ deltaY: 51, clientX: 125, clientY: 75, shiftKey: true });
    vi.advanceTimersByTime(39);
    expect(h.zoom).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(h.zoom).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      deltaY: 51, deltaMode: 0, clientX: 125, clientY: 75, shiftKey: true,
    }));
    expect(h.pan).not.toHaveBeenCalled();
  });

  it.each([false, true])('preference off zooms trackpads and wheels (inversion %s)', (inverted) => {
    const h = harness(false, inverted);
    h.send({ deltaX: 3, deltaY: 5 });
    h.send({ deltaY: 100 }, 8);
    expect(h.pan).not.toHaveBeenCalled();
    expect(h.zoom.mock.calls.map(([event]) => event.deltaY)).toEqual(inverted ? [-5, -100] : [5, 100]);
  });

  it('inverts wheel and pinch zoom exactly once and leaves trackpad pan direction alone', () => {
    const h = harness(true, true);
    h.send({ deltaY: 40, wheelDeltaY: -120, clientX: 72, clientY: 31 });
    h.send({ deltaY: 1.5, ctrlKey: true }, 8);
    h.send({ deltaX: 3, deltaY: 5 }, 500);
    expect(h.zoom.mock.calls.map(([event]) => event.deltaY)).toEqual([-40, -1.5]);
    expect(h.zoom.mock.calls[0][0]).toMatchObject({ clientX: 72, clientY: 31 });
    expect(h.pan.mock.calls).toEqual([[3, 5]]);
  });

  it('ignores a zero-delta gesture boundary instead of latching trackpad', () => {
    const h = harness();
    h.send({ deltaY: 0 });
    h.send({ deltaY: 100 }, 8);
    expect(h.pan).not.toHaveBeenCalled();
    expect(h.zoom.mock.calls[1][0].deltaY).toBe(100);
  });

  it('cancels pending dispatch and removes its listener on cleanup', () => {
    vi.useFakeTimers();
    const h = harness();
    h.send({ deltaY: 51 });
    h.detach();
    vi.runAllTimers();
    expect(h.zoom).not.toHaveBeenCalled();
    h.send({ deltaY: 2 }, 500);
    expect(h.zoom).toHaveBeenCalledTimes(1);
    expect(h.pan).not.toHaveBeenCalled();
  });
});

// Exercise the actual controls used by the canvas, not a second zoom formula.
describe.each(['perspective', 'orthographic'] as const)('%s OrbitControls wheel integration', (projection) => {
  it.each([-40, 40])('retains pre-regression zoom speed/direction and cursor anchor for deltaY %s', (deltaY) => {
    const element = document.createElement('canvas');
    element.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 200 }) as DOMRect;
    const camera = projection === 'perspective'
      ? new PerspectiveCamera(34, 1, 0.1, 1000)
      : new OrthographicCamera(-50, 50, 50, -50, 0.1, 1000);
    camera.position.set(0, 0, 100);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();
    const controls = new OrbitControls(camera, element);
    controls.enableDamping = false;
    controls.zoomSpeed = 1.7;
    controls.zoomToCursor = true;
    cleanups.push(() => controls.dispose());
    const pan = vi.fn();
    cleanups.push(installViewportWheelInput(element, { scrollPans: true, invertWheelZoom: false }, pan));
    const anchor = new Vector3(0.5, 0.25, 0).unproject(camera);
    const before = anchor.clone().project(camera);
    const distance = camera.position.distanceTo(controls.target);
    element.dispatchEvent(wheel({ deltaY, wheelDeltaY: -Math.sign(deltaY) * 120, clientX: 150, clientY: 75 }));
    const expectedScale = 0.95 ** (deltaY < 0 ? 1.7 : -1.7);
    if (camera instanceof PerspectiveCamera) {
      expect(camera.position.distanceTo(controls.target) / distance).toBeCloseTo(expectedScale);
    } else {
      expect(camera.zoom).toBeCloseTo(1 / expectedScale);
    }
    const after = anchor.project(camera);
    expect(after.x).toBeCloseTo(before.x);
    expect(after.y).toBeCloseTo(before.y);
    expect(pan).not.toHaveBeenCalled();
  });
});
