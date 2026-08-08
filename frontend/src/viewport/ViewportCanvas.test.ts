import { Box3, PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it, vi } from 'vitest';
import { calculateCameraFit, clippingRange } from './cameraMath';
import { axisColorsFromTokens, cameraFitDisposition, cameraFitKey, canRenderWebGL, canvasNeedsRemeasure, gizmoAxisDirection, installContextLossFallback, pickGizmoAxis, rebracketCamera, scheduleAppliedTask, shouldShowAxisGizmo, type GizmoAxis } from './ViewportCanvas';

class FakeScheduler {
  private readonly tasks = new Set<() => void>();

  schedule(task?: () => void): () => void {
    if (task) this.tasks.add(task);
    return () => {
      if (task) this.tasks.delete(task);
    };
  }

  flush(): void {
    const pending = [...this.tasks];
    this.tasks.clear();
    pending.forEach((task) => task());
  }
}

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

  it('changes the camera fit key when bounds change without a nonce change', () => {
    const first = new Box3(new Vector3(0, 0, 0), new Vector3(1, 1, 1));
    const second = new Box3(new Vector3(10, 0, 0), new Vector3(12, 2, 2));
    expect(cameraFitKey(first, 3)).not.toBe(cameraFitKey(second, 3));
    expect(cameraFitKey(first, 3)).not.toBe(cameraFitKey(first, 4));
  });

  it('re-schedules a first camera fit cancelled by an identical bounds update', () => {
    const scheduler = new FakeScheduler();
    const applied = { current: null as string | null };
    const cameraPosition = new Vector3();
    const firstBounds = new Box3(new Vector3(-20, -10, 0), new Vector3(20, 10, 80));
    const fitKey = cameraFitKey(firstBounds, 0, 'perspective', 16 / 9);

    const cancelFirstEffect = scheduleAppliedTask(scheduler, applied, fitKey, () => {
      cameraPosition.copy(calculateCameraFit(firstBounds, 'three-quarter', 'perspective', 16 / 9).position);
    });

    cancelFirstEffect();
    const identicalBounds = firstBounds.clone();
    scheduleAppliedTask(scheduler, applied, cameraFitKey(identicalBounds, 0, 'perspective', 16 / 9), () => {
      cameraPosition.copy(calculateCameraFit(identicalBounds, 'three-quarter', 'perspective', 16 / 9).position);
    });
    scheduler.flush();

    const fittedPosition = calculateCameraFit(identicalBounds, 'three-quarter', 'perspective', 16 / 9).position;
    expect(cameraPosition.toArray()).toEqual(fittedPosition.toArray());
  });

  it('does not re-apply a completed fit for equal bounds', () => {
    const scheduler = new FakeScheduler();
    const applied = { current: null as string | null };
    const bounds = new Box3(new Vector3(-20, -10, 0), new Vector3(20, 10, 80));
    const fitKey = cameraFitKey(bounds, 0, 'perspective', 16 / 9);
    const cameraPosition = new Vector3();
    const applyFit = () => {
      cameraPosition.copy(calculateCameraFit(bounds, 'three-quarter', 'perspective', 16 / 9).position);
    };

    scheduleAppliedTask(scheduler, applied, fitKey, applyFit);
    scheduler.flush();

    const orbitedPosition = new Vector3(10, 20, 30);
    cameraPosition.copy(orbitedPosition);
    scheduleAppliedTask(scheduler, applied, cameraFitKey(bounds.clone(), 0, 'perspective', 16 / 9), applyFit);
    scheduler.flush();

    expect(cameraPosition.toArray()).toEqual(orbitedPosition.toArray());
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
    expect(cameraFitDisposition({ fit: null, view: null }, { fit: 'a', view }, false)).toBe('apply');
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'a', view }, false)).toBe('settled');
  });

  it('re-frames a model whose bounds moved while the camera is still the application\'s', () => {
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'b', view }, false)).toBe('apply');
  });

  // Every preview frame during a drag changes the bounds, and the fit is
  // computed from the last requested direction -- so applying it would snap an
  // orbited camera back to the preset several times a second.
  it('leaves a camera the user has moved alone when only the bounds changed', () => {
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'b', view }, true)).toBe('record');
  });

  it('takes the camera back when the user asks for a view', () => {
    expect(cameraFitDisposition({ fit: 'a', view }, { fit: 'b', view: otherView }, true)).toBe('apply');
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
