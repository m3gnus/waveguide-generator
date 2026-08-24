import { Box3, OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it, vi } from 'vitest';
import { clippingRange } from './cameraMath';
import { axisColorsFromTokens, boundsAreDiscontinuous, cameraFitDisposition, cameraFitKey, canRenderWebGL, canvasNeedsRemeasure, gizmoAxisDirection, installContextLossFallback, observeCanvasVisibility, ownCameraProjection, pickGizmoAxis, rebracketCamera, resizeCameraFrustum, shouldShowAxisGizmo, transferCameraView, type GizmoAxis } from './ViewportCanvas';

describe('viewport renderer guards', () => {
  it('probes an actual WebGL2 context rather than constructor globals', () => {
    const supported = { getContext: vi.fn(() => ({})) } as unknown as HTMLCanvasElement;
    const unsupported = { getContext: vi.fn(() => null) } as unknown as HTMLCanvasElement;
    expect(canRenderWebGL(() => supported)).toBe(true);
    expect(canRenderWebGL(() => unsupported)).toBe(false);
    expect(supported.getContext).toHaveBeenCalledWith('webgl2', expect.any(Object));
  });

  it('routes WebGL context loss to the renderer fallback', () => {
    const canvas = document.createElement('canvas');
    const failure = vi.fn();
    installContextLossFallback(canvas, failure);
    const event = new Event('webglcontextlost', { cancelable: true });
    canvas.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(failure).toHaveBeenCalledWith('WebGL2 context was lost');
  });

  it('reports a second loss after the context has been restored', () => {
    const canvas = document.createElement('canvas');
    const failure = vi.fn();
    installContextLossFallback(canvas, failure);
    canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
    canvas.dispatchEvent(new Event('webglcontextrestored'));
    canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
    expect(failure).toHaveBeenCalledTimes(2);
  });

  // React Three Fiber disposes a Canvas by calling forceContextLoss() half a
  // second after unmount. Every Parametric <-> CAD switch made before geometry
  // is ingested unmounts the Canvas, so treating that as a fault left the
  // viewport stuck on "WebGL2 context was lost" once the user switched back.
  it('ignores the context loss react-three-fiber fires while disposing', () => {
    const canvas = document.createElement('canvas');
    const failure = vi.fn();
    const detach = installContextLossFallback(canvas, failure);
    detach();
    canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
    expect(failure).not.toHaveBeenCalled();
  });

  it('changes the camera fit key when bounds change without a nonce change', () => {
    const first = new Box3(new Vector3(0, 0, 0), new Vector3(1, 1, 1));
    const second = new Box3(new Vector3(10, 0, 0), new Vector3(12, 2, 2));
    expect(cameraFitKey(first, 3)).not.toBe(cameraFitKey(second, 3));
    expect(cameraFitKey(first, 3)).not.toBe(cameraFitKey(first, 4));
  });

  it('resolves gizmo axes from the active theme tokens', () => {
    const values: Record<string, string> = {
      '--red-rgb': '168, 54, 48',
      '--green-rgb': '39, 117, 57',
      '--blue-rgb': '56, 93, 184',
    };
    expect(axisColorsFromTokens({ getPropertyValue: (name) => values[name] ?? '' })).toEqual([
      'rgb(168, 54, 48)',
      'rgb(39, 117, 57)',
      'rgb(56, 93, 184)',
    ]);
  });

  it('maps all six clickable gizmo axes to unit camera directions', () => {
    const expected: Array<[GizmoAxis, readonly [number, number, number]]> = [
      ['positive-x', [1, 0, 0]],
      ['negative-x', [-1, 0, 0]],
      ['positive-y', [0, 1, 0]],
      ['negative-y', [0, -1, 0]],
      ['positive-z', [0, 0, 1]],
      ['negative-z', [0, 0, -1]],
    ];
    expected.forEach(([axis, direction]) => expect(gizmoAxisDirection(axis)).toEqual(direction));
  });

  it('hides the canvas gizmo when its viewport pane is narrow or short', () => {
    expect(shouldShowAxisGizmo(600, 269)).toBe(false);
    expect(shouldShowAxisGizmo(601, 240)).toBe(false);
    expect(shouldShowAxisGizmo(601, 241)).toBe(true);
    expect(shouldShowAxisGizmo(646, 269)).toBe(true);
  });
});

describe('cameraFitDisposition', () => {
  const view = '0:perspective:1.6:0.72,0.52,0.72';
  const otherView = '1:perspective:1.6:0,0,1';

  it('applies the first fit, then settles', () => {
    expect(cameraFitDisposition({ fit: null, view: null }, { fit: 'a', view })).toBe('apply');
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'a', view })).toBe('settled');
  });

  it('keeps the original framing when model bounds move', () => {
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'b', view })).toBe('record');
  });

  // Every preview frame during a drag changes the bounds, and the fit is
  // computed from the last requested direction -- so applying it would snap an
  // orbited camera back to the preset several times a second.
  it('leaves a camera the user has moved alone when only the bounds changed', () => {
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'b', view })).toBe('record');
  });

  it('takes the camera back when the user asks for a view', () => {
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'b', view: otherView })).toBe('apply');
  });

  // Switching back from CAD Link refits against the stale pre-switch scene,
  // and the restored parametric frames land a beat later under the same view
  // key. Merely recording those bounds left the model partly out of frame
  // until the user touched the controls.
  describe('escalates record to apply when the scene bounds jump discontinuously', () => {
    const box = (min: [number, number, number], max: [number, number, number]) =>
      new Box3(new Vector3(...min), new Vector3(...max));
    const fitted = box([-100, -100, 0], [100, 100, 200]);

    it('reframes a metres-to-millimetres unit jump', () => {
      const cadMetres = box([-0.1, -0.1, 0], [0.1, 0.1, 0.2]);
      expect(boundsAreDiscontinuous(cadMetres, fitted)).toBe(true);
      expect(boundsAreDiscontinuous(fitted, cadMetres)).toBe(true);
      expect(cameraFitDisposition(
        { fit: 'a', view, bounds: cadMetres },
        { fit: 'b', view, bounds: fitted },
      )).toBe('apply');
    });

    it('reframes a model that no longer overlaps the fitted one', () => {
      const elsewhere = box([500, 500, 500], [700, 700, 700]);
      expect(boundsAreDiscontinuous(fitted, elsewhere)).toBe(true);
      expect(cameraFitDisposition(
        { fit: 'a', view, bounds: fitted },
        { fit: 'b', view, bounds: elsewhere },
      )).toBe('apply');
    });

    it('keeps recording ordinary geometry edits in place', () => {
      const nudged = box([-110, -105, 0], [104, 100, 230]);
      expect(boundsAreDiscontinuous(fitted, nudged)).toBe(false);
      expect(cameraFitDisposition(
        { fit: 'a', view, bounds: fitted },
        { fit: 'b', view, bounds: nudged },
      )).toBe('record');
    });

    it('records when no fitted bounds are known yet', () => {
      expect(cameraFitDisposition(
        { fit: 'a', view, bounds: null },
        { fit: 'b', view, bounds: fitted },
      )).toBe('record');
    });

    it('never outranks an explicit view request', () => {
      expect(cameraFitDisposition(
        { fit: 'a', view, bounds: fitted },
        { fit: 'b', view: otherView, bounds: fitted },
      )).toBe('apply');
    });
  });

  it('re-brackets changed bounds without moving a user-owned camera', () => {
    const camera = new PerspectiveCamera();
    const target = new Vector3();
    camera.position.set(0, 0, 410);
    Object.assign(camera, clippingRange(410, 100));
    const originalPosition = camera.position.clone();
    const updateProjectionMatrix = vi.spyOn(camera, 'updateProjectionMatrix');

    expect(rebracketCamera(camera, target, 500)).toBe(true);
    expect({ near: camera.near, far: camera.far }).toEqual(clippingRange(410, 500));
    expect(camera.position.toArray()).toEqual(originalPosition.toArray());
    expect(updateProjectionMatrix).toHaveBeenCalledOnce();

    expect(rebracketCamera(camera, target, 500)).toBe(false);
    expect(updateProjectionMatrix).toHaveBeenCalledOnce();
  });

  it('resizes perspective and orthographic frustums without moving the camera', () => {
    const perspective = new PerspectiveCamera(34, 1);
    perspective.position.set(10, 20, 30);
    resizeCameraFrustum(perspective, 2);
    expect(perspective.aspect).toBe(2);
    expect(perspective.position.toArray()).toEqual([10, 20, 30]);

    const orthographic = new OrthographicCamera(-100, 100, 50, -50);
    orthographic.position.set(10, 20, 30);
    resizeCameraFrustum(orthographic, 2);
    expect([orthographic.left, orthographic.right, orthographic.top, orthographic.bottom]).toEqual([-100, 100, 50, -50]);
    resizeCameraFrustum(orthographic, 1);
    expect([orthographic.left, orthographic.right, orthographic.top, orthographic.bottom]).toEqual([-50, 50, 50, -50]);
    expect(orthographic.position.toArray()).toEqual([10, 20, 30]);
  });

  it('keeps Fiber from overwriting the fitted frustum before a resize', () => {
    const perspective = ownCameraProjection(new PerspectiveCamera());
    const orthographic = ownCameraProjection(new OrthographicCamera(-100, 100, 50, -50));

    expect((perspective as PerspectiveCamera & { manual?: boolean }).manual).toBe(true);
    expect((orthographic as OrthographicCamera & { manual?: boolean }).manual).toBe(true);
    expect([orthographic.left, orthographic.right, orthographic.top, orthographic.bottom]).toEqual([-100, 100, 50, -50]);
  });

  it('preserves target, orientation, and visible height across projection changes', () => {
    const target = new Vector3(4, 5, 6);
    const perspective = new PerspectiveCamera(34, 16 / 9);
    perspective.position.set(4, 5, 106);
    perspective.lookAt(target);
    const orthographic = new OrthographicCamera(-1, 1, 1, -1);

    transferCameraView(perspective, orthographic, target, 16 / 9);
    expect(orthographic.position.toArray()).toEqual(perspective.position.toArray());
    expect(orthographic.getWorldDirection(new Vector3()).angleTo(perspective.getWorldDirection(new Vector3()))).toBeLessThan(1e-8);

    const roundTrip = new PerspectiveCamera(34, 16 / 9);
    transferCameraView(orthographic, roundTrip, target, 16 / 9);
    expect(roundTrip.position.distanceTo(perspective.position)).toBeLessThan(1e-8);
    expect(roundTrip.getWorldDirection(new Vector3()).angleTo(perspective.getWorldDirection(new Vector3()))).toBeLessThan(1e-8);
  });
});

describe('pickGizmoAxis', () => {
  // A camera looking down -Z: screen right is +X, screen up is +Y.
  const right = new Vector3(1, 0, 0);
  const up = new Vector3(0, 1, 0);
  const centre: [number, number] = [50, 200];

  it('picks the head under the pointer for every axis', () => {
    expect(pickGizmoAxis([50 + 40, 200], centre, right, up)).toBe('positive-x');
    expect(pickGizmoAxis([50 - 40, 200], centre, right, up)).toBe('negative-x');
    expect(pickGizmoAxis([50, 200 - 40], centre, right, up)).toBe('positive-y');
    expect(pickGizmoAxis([50, 200 + 40], centre, right, up)).toBe('negative-y');
    // +Z and -Z project onto the centre for this camera; one of them wins.
    expect(pickGizmoAxis([50, 200], centre, right, up)).toMatch(/^(positive|negative)-z$/);
  });

  it('ignores pointers that are not on a head', () => {
    expect(pickGizmoAxis([50 + 40, 200 + 40], centre, right, up)).toBeNull();
    expect(pickGizmoAxis([400, 400], centre, right, up)).toBeNull();
    // Just outside the hit radius of +X.
    expect(pickGizmoAxis([50 + 40 + 16, 200], centre, right, up)).toBeNull();
  });

  it('prefers the head facing the camera when two overlap', () => {
    // Looking down -Z, +Z points at the viewer and must win over -Z.
    expect(pickGizmoAxis([50, 200], centre, right, up)).toBe('positive-z');
  });
});

describe('canvasNeedsRemeasure', () => {
  const box = (clientWidth: number, clientHeight: number) => ({ clientWidth, clientHeight });

  it('spots the cold-start canvas left at the HTML default inside a real panel', () => {
    expect(canvasNeedsRemeasure(box(764, 432), box(300, 150))).toBe(true);
  });

  it('stays quiet once the canvas matches, so the nudge cannot loop', () => {
    expect(canvasNeedsRemeasure(box(764, 432), box(764, 432))).toBe(false);
    expect(canvasNeedsRemeasure(box(764, 432), box(764, 433))).toBe(false);
  });

  it('does not fire before the container itself has been laid out', () => {
    expect(canvasNeedsRemeasure(box(0, 0), box(300, 150))).toBe(false);
    expect(canvasNeedsRemeasure(box(764, 432), null)).toBe(false);
  });
});

describe('repaint when the canvas is shown again', () => {
  /**
   * frameloop="demand" over a context without preserveDrawingBuffer means the
   * drawing buffer is gone once the browser has composited it, and demand
   * rendering has no reason to draw another. Any period where the canvas is not
   * composited -- an inactive dock tab, a backgrounded window -- therefore ends
   * on an empty canvas unless something asks for a frame.
   */
  function withIntersectionObserver(run: (trigger: (isIntersecting: boolean) => void) => void): void {
    const originalObserver = globalThis.IntersectionObserver;
    let callback: ((entries: Array<{ isIntersecting: boolean }>) => void) | null = null;
    const disconnect = vi.fn();
    class FakeObserver {
      constructor(handler: (entries: Array<{ isIntersecting: boolean }>) => void) { callback = handler; }
      observe() { /* the element is irrelevant to the fake */ }
      disconnect = disconnect;
    }
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = FakeObserver as never;
    try {
      run((isIntersecting) => callback?.([{ isIntersecting }]));
    } finally {
      (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = originalObserver;
    }
  }

  it('asks for a frame when the canvas comes back into view, and not while it is hidden', () => {
    withIntersectionObserver((trigger) => {
      const onShown = vi.fn();
      const stop = observeCanvasVisibility(document.createElement('canvas'), onShown);
      trigger(false);
      expect(onShown).not.toHaveBeenCalled();
      trigger(true);
      expect(onShown).toHaveBeenCalledOnce();
      stop();
    });
  });

  it('asks for a frame when the document becomes visible again', () => {
    withIntersectionObserver(() => {
      const onShown = vi.fn();
      const stop = observeCanvasVisibility(document.createElement('canvas'), onShown);
      const hidden = vi.spyOn(document, 'hidden', 'get');

      hidden.mockReturnValue(true);
      document.dispatchEvent(new Event('visibilitychange'));
      expect(onShown).not.toHaveBeenCalled();

      hidden.mockReturnValue(false);
      document.dispatchEvent(new Event('visibilitychange'));
      expect(onShown).toHaveBeenCalledOnce();

      // Cleanup has to detach the document listener, not just the observer.
      stop();
      document.dispatchEvent(new Event('visibilitychange'));
      expect(onShown).toHaveBeenCalledOnce();
      hidden.mockRestore();
    });
  });
});
