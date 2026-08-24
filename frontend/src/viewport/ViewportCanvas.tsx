import { GizmoHelper, GizmoViewport, OrbitControls } from '@react-three/drei';
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Component, memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentRef, type ErrorInfo, type ReactNode, type RefObject } from 'react';
import { Box3, CanvasTexture, MathUtils, OrthographicCamera, PerspectiveCamera, Plane, Vector3 } from 'three';
import { readChartTokens } from '../results/EChart';
import type { CameraProjection, ViewerPreferences } from '../viewerprefs/viewerPreferences';
import { calculateCameraFit, clippingRange, presetDirection, viewDirection, zoomedOrthographicValue, type CameraDirection } from './cameraMath';
import { DemandRenderScheduler, installViewportTestHook } from './demandRender';
import { FieldPlane } from './FieldPlane';
import { deriveCapQuad, fieldPlaneToClipPlane, staticSectionClipPlane, type ModelClipMode } from './fieldPlaneClipping';
import { maskMatchesGeometry, useFieldPlaneMaskStore } from './fieldPlaneMaskStore';
import { useFieldPlaneStore } from './fieldPlaneStore';
import type { FrameScene } from './frameScene';
import { createMaterialLibrary } from './materials';
import { SurfaceMesh } from './SurfaceMesh';
import type { CameraPreset, DisplayMode, ViewportTheme } from './types';

export type CameraRequest =
  | { preset: CameraPreset; direction?: never; nonce: number }
  | { preset?: never; direction: CameraDirection; nonce: number };

export interface ZoomRequest {
  direction: 'in' | 'out';
  nonce: number;
}

interface ViewportCanvasProps {
  scene: FrameScene;
  sceneMarker: string;
  mode: DisplayMode;
  showEnclosure: boolean;
  clipMode: ModelClipMode;
  invertFieldClip: boolean;
  cameraRequest: CameraRequest;
  zoomRequest: ZoomRequest;
  cameraProjection: CameraProjection;
  preferences: ViewerPreferences;
  frameStartedAt: number | null;
  onClientFrame: (milliseconds: number) => void;
  theme: ViewportTheme;
  onRenderFailure: (message: string) => void;
  onCameraDirection: (direction: CameraDirection) => void;
}

let cachedWebGL2Support: boolean | null = null;

export function resetWebGLProbe(): void { cachedWebGL2Support = null; }

export function canRenderWebGL(createCanvas?: () => HTMLCanvasElement): boolean {
  if (!createCanvas && cachedWebGL2Support !== null) return cachedWebGL2Support;
  if (typeof document === 'undefined') return false;
  if (!createCanvas && typeof WebGL2RenderingContext === 'undefined') {
    cachedWebGL2Support = false;
    return false;
  }
  let supported = false;
  try {
    const canvas = (createCanvas ?? (() => document.createElement('canvas')))();
    supported = Boolean(canvas.getContext('webgl2', { antialias: false, depth: false, stencil: false }));
  } catch {
    supported = false;
  }
  if (!createCanvas) cachedWebGL2Support = supported;
  return supported;
}

export function cameraFitKey(bounds: FrameScene['bounds'], nonce: number, projection: CameraProjection = 'perspective', aspect = 1): string {
  return `${nonce}:${projection}:${aspect}:${bounds.min.toArray().join(',')}:${bounds.max.toArray().join(',')}`;
}

/** How far apart bounding-sphere radii may drift before a scene change stops
 * looking like an edit of the same geometry. Generous on purpose: parametric
 * edits move a bound by percents, while the transitions this exists to catch
 * (CAD metres against parametric millimetres, a different model entirely)
 * shift it by orders of magnitude. */
const BOUNDS_CONTINUITY_RATIO = 2;

/**
 * True when new scene bounds cannot plausibly be a routine geometry edit of
 * the bounds the camera was last fitted to: the bounding-sphere radius jumped
 * by more than BOUNDS_CONTINUITY_RATIO in either direction, or the boxes do
 * not even intersect. Either way the retained framing shows some other
 * region of space, so keeping it would leave the model partly (or entirely)
 * out of view.
 */
export function boundsAreDiscontinuous(applied: Box3, next: Box3): boolean {
  const radius = (box: Box3) => Math.max(box.min.distanceTo(box.max) / 2, 0.5);
  const ratio = radius(next) / radius(applied);
  if (ratio > BOUNDS_CONTINUITY_RATIO || ratio < 1 / BOUNDS_CONTINUITY_RATIO) return true;
  return !applied.intersectsBox(next);
}

/**
 * What to do with a camera fit whose inputs have changed.
 *
 * The first scene is framed automatically. After that, new preview bounds,
 * projection changes, and panel resizes keep the same visible view so geometry
 * revisions can be compared in place. Only an explicit view request (preset or
 * gizmo axis, represented by a new view key) frames the model again — unless
 * the new bounds are discontinuous with the fitted ones (see
 * `boundsAreDiscontinuous`), which happens when a scene swap lands *after* the
 * refit that announced it: switching back from CAD Link fits the camera
 * against the stale pre-switch scene, and the restored model's frames arrive
 * a beat later under the same view key. Recording those bounds would leave
 * the model partly out of frame until the user touches the controls.
 *
 * `settled` means the key is already applied. `record` remembers changed fit
 * inputs without moving the camera. `apply` means the requested direction or
 * reset nonce changed — or the scene itself discontinuously did — and
 * therefore intentionally takes the camera back.
 */
export function cameraFitDisposition(
  applied: { fit: string | null; view: string | null; bounds?: Box3 | null },
  next: { fit: string; view: string; bounds?: Box3 },
): 'settled' | 'record' | 'apply' {
  if (applied.fit === next.fit) return 'settled';
  if (applied.view !== next.view) return 'apply';
  if (applied.bounds && next.bounds && boundsAreDiscontinuous(applied.bounds, next.bounds)) return 'apply';
  return 'record';
}

/** Preserve the visible view while swapping between camera projection types. */
export function transferCameraView(
  previous: PerspectiveCamera | OrthographicCamera,
  next: PerspectiveCamera | OrthographicCamera,
  target: Vector3,
  aspect: number,
): void {
  const offset = previous.position.clone().sub(target);
  if (offset.lengthSq() === 0) offset.set(0, 0, 1);
  const verticalSpan = previous instanceof PerspectiveCamera
    ? 2 * offset.length() * Math.tan(MathUtils.degToRad(previous.fov / 2))
    : (previous.top - previous.bottom) / Math.max(previous.zoom, 1e-6);

  next.up.copy(previous.up);
  if (next instanceof PerspectiveCamera) {
    const distance = verticalSpan / (2 * Math.tan(MathUtils.degToRad(next.fov / 2)));
    next.position.copy(target).add(offset.normalize().multiplyScalar(distance));
  } else {
    next.position.copy(previous.position);
    next.zoom = 1;
    const halfHeight = Math.max(verticalSpan / 2, 1e-6);
    next.left = -halfHeight * Math.max(aspect, 0.01);
    next.right = halfHeight * Math.max(aspect, 0.01);
    next.top = halfHeight;
    next.bottom = -halfHeight;
  }
  next.lookAt(target);
  next.updateProjectionMatrix();
}

/** Resize the frustum without changing position, orientation, target, or zoom. */
export function resizeCameraFrustum(camera: PerspectiveCamera | OrthographicCamera, aspect: number): void {
  const safeAspect = Math.max(aspect, 0.01);
  if (camera instanceof PerspectiveCamera) {
    camera.aspect = safeAspect;
  } else {
    const centerX = (camera.left + camera.right) / 2;
    const halfHeight = (camera.top - camera.bottom) / 2;
    camera.left = centerX - halfHeight * safeAspect;
    camera.right = centerX + halfHeight * safeAspect;
  }
  camera.updateProjectionMatrix();
}

/** Keep React Three Fiber from replacing the fitted frustum on canvas resize. */
export function ownCameraProjection<T extends PerspectiveCamera | OrthographicCamera>(camera: T): T {
  // `manual` is React Three Fiber's camera escape hatch. Without it, Fiber
  // rewrites an orthographic camera to canvas-pixel bounds before CameraRig's
  // resize effect runs, permanently losing the fitted vertical span.
  (camera as T & { manual?: boolean }).manual = true;
  return camera;
}

/** Update only the depth bracket, preserving a user-owned position and target. */
export function rebracketCamera(
  camera: PerspectiveCamera | OrthographicCamera,
  target: Vector3,
  boundsRadius: number,
): boolean {
  const { near, far } = clippingRange(camera.position.distanceTo(target), boundsRadius);
  if (camera.near === near && camera.far === far) return false;
  camera.near = near;
  camera.far = far;
  camera.updateProjectionMatrix();
  return true;
}

/**
 * Report a genuine loss of the drawing context, and return a detacher.
 *
 * Detaching matters as much as reporting. React Three Fiber tears a Canvas down
 * by calling `forceContextLoss()` half a second after unmount, which dispatches
 * this very event on the canvas it is disposing. That is routine teardown, not a
 * graphics fault -- and the viewport unmounts its Canvas whenever the active
 * scene goes empty, which includes every Parametric -> CAD switch made before
 * any geometry has been ingested. Reporting it left the panel permanently
 * showing "WebGL2 context was lost" over a scene that was rendering perfectly.
 *
 * The listener is therefore owned by the component that mounted the Canvas and
 * removed with it, so the disposal event lands after the listener is gone.
 */
export function installContextLossFallback(canvas: HTMLCanvasElement, onFailure: (message: string) => void): () => void {
  // Not `{ once: true }`: a context can be lost again after being restored, and
  // the second loss deserves the same notice as the first.
  const onLost = (event: Event) => {
    // Preventing the default is what lets the browser restore the context.
    event.preventDefault();
    onFailure('WebGL2 context was lost');
  };
  canvas.addEventListener('webglcontextlost', onLost);
  return () => canvas.removeEventListener('webglcontextlost', onLost);
}

export function installWheelZoomInversion(element: HTMLElement, enabled: boolean): () => void {
  if (!enabled) return () => undefined;
  const redispatched = new WeakSet<Event>();
  const invert = (event: WheelEvent) => {
    if (redispatched.has(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const replacement = new WheelEvent('wheel', {
      deltaMode: event.deltaMode,
      deltaX: event.deltaX,
      deltaY: -event.deltaY,
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
  };
  element.addEventListener('wheel', invert, { capture: true, passive: false });
  return () => element.removeEventListener('wheel', invert, { capture: true });
}

type OrbitControlsInstance = ComponentRef<typeof OrbitControls>;

const fallbackAxisColors: [string, string, string] = ['rgb(255, 111, 96)', 'rgb(90, 212, 141)', 'rgb(111, 157, 255)'];

export function axisColorsFromTokens(style: Pick<CSSStyleDeclaration, 'getPropertyValue'>): [string, string, string] {
  return (['red', 'green', 'blue'] as const).map((name, index) => {
    const channels = style.getPropertyValue(`--${name}-rgb`).trim();
    return channels ? `rgb(${channels})` : fallbackAxisColors[index];
  }) as [string, string, string];
}

export function shouldShowAxisGizmo(viewportWidth: number, viewportHeight: number): boolean {
  return viewportWidth > 600 && viewportHeight > 240;
}

export type GizmoAxis = 'positive-x' | 'negative-x' | 'positive-y' | 'negative-y' | 'positive-z' | 'negative-z';

const gizmoDirections: Record<GizmoAxis, CameraDirection> = {
  'positive-x': [1, 0, 0],
  'negative-x': [-1, 0, 0],
  'positive-y': [0, 1, 0],
  'negative-y': [0, -1, 0],
  'positive-z': [0, 0, 1],
  'negative-z': [0, 0, -1],
};

export function gizmoAxisDirection(axis: GizmoAxis): CameraDirection {
  return gizmoDirections[axis];
}

/** Gizmo placement, shared by the drei helper and the DOM-level hit test below. */
export const GIZMO_MARGIN: [number, number] = [50, 125];
/** drei's Hud camera maps one world unit to one pixel, and the heads sit at scale 40. */
export const GIZMO_RADIUS_PX = 40;
const GIZMO_HIT_RADIUS_PX = 15;

/**
 * Which axis head, if any, sits under a pointer.
 *
 * The heads live inside drei's `Hud`, which renders into a portal scene whose
 * pointer events never reach them here — verified in a real browser, where
 * neither clicking nor hovering a head produced any response. The gizmo is a
 * pure orthographic projection of the world axes, so picking it is just
 * arithmetic: project each unit axis onto the camera's right/up vectors and
 * take the nearest within a hit radius. Doing it ourselves keeps the gizmo
 * clickable without depending on drei's portal event plumbing.
 */
export function pickGizmoAxis(
  pointer: [number, number],
  centre: [number, number],
  cameraRight: Vector3,
  cameraUp: Vector3,
  radiusPx = GIZMO_RADIUS_PX,
  hitRadiusPx = GIZMO_HIT_RADIUS_PX,
): GizmoAxis | null {
  let best: { axis: GizmoAxis; distance: number; depth: number } | null = null;
  (Object.keys(gizmoDirections) as GizmoAxis[]).forEach((axis) => {
    const [x, y, z] = gizmoDirections[axis];
    const direction = new Vector3(x, y, z);
    const screenX = centre[0] + radiusPx * direction.dot(cameraRight);
    const screenY = centre[1] - radiusPx * direction.dot(cameraUp);
    const distance = Math.hypot(pointer[0] - screenX, pointer[1] - screenY);
    if (distance > hitRadiusPx) return;
    // Two heads overlap when an axis points at the camera; prefer the near one.
    const depth = direction.dot(cameraRight.clone().cross(cameraUp));
    if (!best || distance < best.distance || (distance === best.distance && depth > best.depth)) {
      best = { axis, distance, depth };
    }
  });
  return best === null ? null : (best as { axis: GizmoAxis }).axis;
}

function cameraUp(direction: Vector3): Vector3 {
  if (Math.abs(direction.y) < 0.999) return new Vector3(0, 1, 0);
  return new Vector3(0, 0, direction.y > 0 ? -1 : 1);
}

function CameraRig({ bounds, request, zoomRequest, projection, preferences, scheduler, controls }: {
  bounds: FrameScene['bounds'];
  request: CameraRequest;
  zoomRequest: ZoomRequest;
  projection: CameraProjection;
  preferences: ViewerPreferences;
  scheduler: DemandRenderScheduler;
  controls: RefObject<OrbitControlsInstance | null>;
}) {
  const set = useThree((state) => state.set);
  const get = useThree((state) => state.get);
  const canvasSize = useThree((state) => state.size);
  const gl = useThree((state) => state.gl);
  const aspect = Math.max(canvasSize.width / Math.max(canvasSize.height, 1), 0.01);
  const cameras = useRef<{
    perspective: PerspectiveCamera;
    orthographic: OrthographicCamera;
  } | null>(null);
  if (cameras.current === null) {
    cameras.current = {
      perspective: ownCameraProjection(new PerspectiveCamera(34, 1, 0.001, 100_000)),
      orthographic: ownCameraProjection(new OrthographicCamera(-1, 1, 1, -1, 0.001, 100_000)),
    };
  }
  const camera = cameras.current[projection];
  const { x: minX, y: minY, z: minZ } = bounds.min;
  const { x: maxX, y: maxY, z: maxZ } = bounds.max;
  const center = useMemo(() => new Vector3(
    (minX + maxX) / 2,
    (minY + maxY) / 2,
    (minZ + maxZ) / 2,
  ), [maxX, maxY, maxZ, minX, minY, minZ]);
  const boundsRadius = useMemo(
    () => Math.max(Math.hypot(maxX - minX, maxY - minY, maxZ - minZ) / 2, 0.5),
    [maxX, maxY, maxZ, minX, minY, minZ],
  );
  const requestedPreset = request.preset;
  const requestedDirection = request.direction;
  const directionX = requestedDirection?.[0] ?? 0;
  const directionY = requestedDirection?.[1] ?? 0;
  const directionZ = requestedDirection?.[2] ?? 0;
  const direction = useMemo(() => requestedPreset === undefined
    ? viewDirection([directionX, directionY, directionZ])
    : presetDirection(requestedPreset), [directionX, directionY, directionZ, requestedPreset]);
  const appliedRequest = useRef<string | null>(null);
  const appliedView = useRef<string | null>(null);
  /** The bounds the camera was last *fitted* to (never merely recorded), so a
   * later scene swap can be told apart from an edit of the framed geometry. */
  const appliedBounds = useRef<Box3 | null>(null);
  const appliedZoom = useRef(zoomRequest.nonce);
  const viewTarget = useRef<Vector3 | null>(null);
  if (viewTarget.current === null) viewTarget.current = center.clone();
  const fitKey = cameraFitKey(bounds, request.nonce, projection, aspect);
  const viewKey = `${request.nonce}:${direction.toArray().join(',')}`;
  const fitRequestKey = `${fitKey}:${direction.toArray().join(',')}`;
  const fit = useMemo(
    () => calculateCameraFit(bounds, direction, projection, aspect),
    [aspect, direction, fitKey, projection],
  );

  useLayoutEffect(() => {
    const previous = get().camera;
    const target = viewTarget.current ?? center;
    if (previous !== camera
      && (previous === cameras.current?.perspective || previous === cameras.current?.orthographic)) {
      transferCameraView(previous, camera, target, aspect);
    }
    set({ camera });
    controls.current?.target.copy(target);
    controls.current?.update();
  }, [aspect, camera, center, controls, get, set]);

  useLayoutEffect(() => {
    resizeCameraFrustum(camera, aspect);
    scheduler.schedule();
  }, [aspect, camera, scheduler]);

  useLayoutEffect(() => {
    const disposition = cameraFitDisposition(
      { fit: appliedRequest.current, view: appliedView.current, bounds: appliedBounds.current },
      { fit: fitRequestKey, view: viewKey, bounds },
    );
    if (disposition === 'settled') return;
    if (disposition === 'record') {
      if (rebracketCamera(camera, controls.current?.target ?? center, boundsRadius)) scheduler.schedule();
      appliedRequest.current = fitRequestKey;
      return;
    }
    appliedView.current = viewKey;
    appliedBounds.current = bounds.clone();
    camera.position.copy(fit.position);
    camera.up.copy(cameraUp(direction));
    camera.lookAt(fit.center);
    if (camera instanceof PerspectiveCamera) {
      camera.aspect = aspect;
    } else if (camera instanceof OrthographicCamera) {
      camera.left = fit.left;
      camera.right = fit.right;
      camera.top = fit.top;
      camera.bottom = fit.bottom;
      camera.zoom = 1;
    }
    camera.near = fit.near;
    camera.far = fit.far;
    camera.updateProjectionMatrix();
    viewTarget.current?.copy(fit.center);
    controls.current?.target.copy(fit.center);
    controls.current?.update();
    appliedRequest.current = fitRequestKey;
    scheduler.schedule();
  }, [aspect, bounds, boundsRadius, camera, center, direction, fit, fitRequestKey, scheduler, viewKey]);

  // The first fit may run before the sibling OrbitControls ref is attached.
  // Apply only the target from the most recent explicit view request; geometry
  // updates must never recenter a target the user has panned.
  useEffect(() => {
    const instance = controls.current;
    if (!instance || !viewTarget.current) return;
    instance.target.copy(viewTarget.current);
    instance.update();
    scheduler.schedule();
  }, [request.nonce, scheduler]);

  useLayoutEffect(() => {
    if (appliedZoom.current === zoomRequest.nonce) return;
    const target = controls.current?.target ?? center;
    if (camera instanceof OrthographicCamera) {
      camera.zoom = zoomedOrthographicValue(camera.zoom, zoomRequest.direction);
      camera.updateProjectionMatrix();
    } else {
      const factor = zoomRequest.direction === 'in' ? 0.8 : 1.25;
      camera.position.sub(target).multiplyScalar(factor).add(target);
    }
    controls.current?.update();
    appliedZoom.current = zoomRequest.nonce;
    scheduler.schedule();
  }, [camera, center, scheduler, zoomRequest.direction, zoomRequest.nonce]);

  useEffect(() => {
    const instance = controls.current;
    if (!instance) return;
    // OrbitControls.stopListenToKeyEvents() dereferences its key-event target
    // unconditionally and THROWS when listenToKeyEvents was never called
    // (null target) — a real-browser-only crash that killed the whole Canvas
    // subtree during commit (jsdom never exercises this path). Guard both the
    // toggle-off and the unmount cleanup on having actually listened.
    if (preferences.keyboardPanEnabled) {
      instance.listenToKeyEvents(gl.domElement);
      return () => {
        try { instance.stopListenToKeyEvents(); } catch { /* target already gone */ }
      };
    }
    return undefined;
  }, [gl.domElement, preferences.keyboardPanEnabled]);

  useEffect(() => installWheelZoomInversion(gl.domElement, preferences.invertWheelZoom), [gl.domElement, preferences.invertWheelZoom]);

  // Fit-time planes go stale the moment the user dollies, and a fixed pair wide
  // enough for every distance wastes the whole depth buffer. Re-bracket the
  // model against the camera's actual distance whenever the controls move.
  const rebracket = () => {
    rebracketCamera(camera, controls.current?.target ?? center, boundsRadius);
  };

  return <OrbitControls
    ref={controls}
    camera={camera}
    makeDefault
    rotateSpeed={preferences.rotateSpeed}
    zoomSpeed={preferences.zoomSpeed}
    panSpeed={preferences.panSpeed}
    enableDamping={preferences.dampingEnabled}
    dampingFactor={preferences.dampingFactor}
    zoomToCursor
    onChange={() => {
      if (controls.current && viewTarget.current) viewTarget.current.copy(controls.current.target);
      rebracket();
      scheduler.schedule();
    }}
  />;
}

function InteractiveAxisHead({ axis, color, label, labelColor, onDirection }: {
  axis: GizmoAxis;
  color: string;
  label?: string;
  labelColor: string;
  onDirection: (direction: CameraDirection) => void;
}) {
  const gl = useThree((state) => state.gl);
  const [active, setActive] = useState(false);
  const texture = useMemo(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const context = canvas.getContext('2d');
    if (context) {
      context.beginPath();
      context.arc(32, 32, 16, 0, 2 * Math.PI);
      context.closePath();
      context.fillStyle = color;
      context.fill();
      if (label) {
        context.font = '18px Inter var, Arial, sans-serif';
        context.textAlign = 'center';
        context.fillStyle = labelColor;
        context.fillText(label, 32, 41);
      }
    }
    return new CanvasTexture(canvas);
  }, [color, label, labelColor]);
  useEffect(() => () => texture.dispose(), [texture]);
  const direction = gizmoAxisDirection(axis);
  const scale = (label ? 1 : 0.75) * (active ? 1.2 : 1);
  return <sprite
    position={direction}
    scale={scale}
    onPointerOver={(event: ThreeEvent<PointerEvent>) => {
      event.stopPropagation();
      setActive(true);
    }}
    onPointerOut={(event: ThreeEvent<PointerEvent>) => {
      event.stopPropagation();
      setActive(false);
    }}
    onPointerDown={(event: ThreeEvent<PointerEvent>) => {
      event.stopPropagation();
      onDirection(direction);
    }}
  >
    <spriteMaterial
      map={texture}
      map-anisotropy={gl.capabilities.getMaxAnisotropy() || 1}
      alphaTest={0.3}
      opacity={label ? 1 : 0.75}
      toneMapped={false}
    />
  </sprite>;
}

function InteractiveAxisHeads({ axisColors, labelColor, onDirection }: {
  axisColors: [string, string, string];
  labelColor: string;
  onDirection: (direction: CameraDirection) => void;
}) {
  return <group scale={40}>
    <InteractiveAxisHead axis="positive-x" color={axisColors[0]} label="X" labelColor={labelColor} onDirection={onDirection} />
    <InteractiveAxisHead axis="positive-y" color={axisColors[1]} label="Y" labelColor={labelColor} onDirection={onDirection} />
    <InteractiveAxisHead axis="positive-z" color={axisColors[2]} label="Z" labelColor={labelColor} onDirection={onDirection} />
    <InteractiveAxisHead axis="negative-x" color={axisColors[0]} labelColor={labelColor} onDirection={onDirection} />
    <InteractiveAxisHead axis="negative-y" color={axisColors[1]} labelColor={labelColor} onDirection={onDirection} />
    <InteractiveAxisHead axis="negative-z" color={axisColors[2]} labelColor={labelColor} onDirection={onDirection} />
  </group>;
}

/**
 * Turns clicks near a gizmo axis head into a camera direction request.
 *
 * Lives outside `GizmoHelper` on purpose: inside the Hud portal `state.camera`
 * is the gizmo's own orthographic camera, and the hit test needs the real one.
 */
function GizmoAxisPicker({ onDirection }: { onDirection: (direction: CameraDirection) => void }) {
  const gl = useThree((state) => state.gl);
  const camera = useThree((state) => state.camera);
  const { width, height } = useThree((state) => state.size);
  const visible = shouldShowAxisGizmo(width, height);

  useEffect(() => {
    if (!visible) return;
    const element = gl.domElement;
    const centre: [number, number] = [GIZMO_MARGIN[0], height - GIZMO_MARGIN[1]];
    const onPointerDown = (event: PointerEvent) => {
      const bounds = element.getBoundingClientRect();
      const right = new Vector3().setFromMatrixColumn(camera.matrixWorld, 0).normalize();
      const up = new Vector3().setFromMatrixColumn(camera.matrixWorld, 1).normalize();
      const axis = pickGizmoAxis(
        [event.clientX - bounds.left, event.clientY - bounds.top],
        centre,
        right,
        up,
      );
      if (!axis) return;
      // Claim the press so OrbitControls does not also start an orbit drag.
      event.preventDefault();
      event.stopPropagation();
      onDirection(gizmoAxisDirection(axis));
    };
    element.addEventListener('pointerdown', onPointerDown, { capture: true });
    return () => element.removeEventListener('pointerdown', onPointerDown, { capture: true });
  }, [camera, gl, height, onDirection, visible, width]);

  return null;
}

function CameraGizmo({ controls, center, scheduler, theme, onDirection }: {
  controls: RefObject<OrbitControlsInstance | null>;
  center: Vector3;
  scheduler: DemandRenderScheduler;
  theme: ViewportTheme;
  onDirection: (direction: CameraDirection) => void;
}) {
  const { width, height } = useThree((state) => state.size);
  const axisColors = useMemo(() => typeof document === 'undefined'
    ? fallbackAxisColors
    : axisColorsFromTokens(getComputedStyle(document.documentElement)), [theme]);
  const labelColor = theme === 'light' ? '#f1f2ed' : '#191817';
  if (!shouldShowAxisGizmo(width, height)) return null;
  return <GizmoHelper
    alignment="bottom-left"
    margin={GIZMO_MARGIN}
    onTarget={() => controls.current?.target.clone() ?? center.clone()}
    onUpdate={() => {
      controls.current?.update();
      scheduler.schedule();
    }}
  >
    <GizmoViewport
      axisColors={axisColors}
      labelColor={labelColor}
      hideAxisHeads
      disabled={false}
    />
    <InteractiveAxisHeads axisColors={axisColors} labelColor={labelColor} onDirection={onDirection} />
  </GizmoHelper>;
}

function PaintObserver({ marker, startedAt, onClientFrame }: {
  marker: string;
  startedAt: number | null;
  onClientFrame: (milliseconds: number) => void;
}) {
  const reported = useRef('');
  useFrame(() => {
    if (reported.current === marker || startedAt === null) return;
    reported.current = marker;
    onClientFrame(Math.max(0, performance.now() - startedAt));
  });
  return null;
}

/**
 * Repaints when the canvas comes back into view.
 *
 * The renderer is `frameloop="demand"` over a WebGL context without
 * `preserveDrawingBuffer`, so the drawing buffer is discarded once the browser
 * has composited it. That is the right trade for a viewport that only changes
 * when the design does -- but it means any period where the canvas is not being
 * composited ends with an empty canvas, and demand rendering has, by
 * definition, no reason to draw another frame.
 *
 * The dock hides an inactive panel, so this bites whenever the viewport shares
 * a tab group: below 800px every panel collapses into one, and switching to the
 * VIEWPORT tab showed an empty black rectangle until the user happened to drag
 * in it. useCanvasRemeasure does not cover this, because on the way back the
 * canvas is already the right size -- nothing needs remeasuring, only redrawing.
 */
export function observeCanvasVisibility(canvas: Element, onShown: () => void): () => void {
  const cleanups: Array<() => void> = [];
  if (typeof IntersectionObserver !== 'undefined') {
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) onShown();
    });
    observer.observe(canvas);
    cleanups.push(() => observer.disconnect());
  }
  if (typeof document !== 'undefined') {
    // Tab-away discards the buffer for the same reason a hidden panel does.
    const onVisibility = () => { if (!document.hidden) onShown(); };
    document.addEventListener('visibilitychange', onVisibility);
    cleanups.push(() => document.removeEventListener('visibilitychange', onVisibility));
  }
  return () => cleanups.forEach((cleanup) => cleanup());
}

function useRepaintWhenShown(scheduler: DemandRenderScheduler): void {
  const gl = useThree((state) => state.gl);
  useEffect(() => observeCanvasVisibility(gl.domElement, () => scheduler.schedule()), [gl, scheduler]);
}

function Scene({ scene, sceneMarker, mode, showEnclosure, clipMode, invertFieldClip, cameraRequest, zoomRequest, cameraProjection, preferences, frameStartedAt, onClientFrame, theme, onCameraDirection }: Omit<ViewportCanvasProps, 'onRenderFailure'>) {
  const invalidate = useThree((state) => state.invalidate);
  const scheduler = useMemo(() => new DemandRenderScheduler(invalidate), [invalidate]);
  useFrame(() => scheduler.flush(), 0);
  const fieldPlane = useFieldPlaneStore((state) => state.plane);
  const fieldJobId = useFieldPlaneStore((state) => state.jobId);
  const fieldGeometrySha256 = useFieldPlaneStore((state) => state.geometrySha256);
  const maskState = useFieldPlaneMaskStore((state) => state);
  const stableClipPlane = useMemo(() => new Plane(new Vector3(1, 0, 0), 0), []);
  const clipDefinition = useMemo(() => {
    if (clipMode === 'section') return staticSectionClipPlane();
    if (clipMode === 'field-plane' && fieldPlane) {
      return fieldPlaneToClipPlane(fieldPlane, scene.unitsPerMetre, invertFieldClip);
    }
    return null;
  }, [clipMode, fieldPlane, invertFieldClip, scene.unitsPerMetre]);
  const clipActive = clipDefinition !== null;
  const materials = useMemo(
    () => createMaterialLibrary(
      mode, clipActive ? stableClipPlane : null, theme,
      preferences.tintSolvedRegion, preferences.sourceRoleColors,
    ),
    [clipActive, mode, preferences.sourceRoleColors, preferences.tintSolvedRegion, stableClipPlane, theme],
  );
  const fieldColormap = useMemo(() => readChartTokens().colormap, [theme]);
  const center = useMemo(() => scene.bounds.getCenter(new Vector3()), [scene.bounds]);
  const size = useMemo(() => scene.bounds.getSize(new Vector3()), [scene.bounds]);
  const controls = useRef<OrbitControlsInstance>(null);
  const capQuad = useMemo(() => clipDefinition ? deriveCapQuad(clipDefinition, scene.bounds, {
    preferredAxisU: clipMode === 'field-plane' && fieldPlane
      ? new Vector3(...fieldPlane.axis_u)
      : undefined,
  }) : null, [clipDefinition, clipMode, fieldPlane, scene.bounds]);
  const capTrusted = clipMode === 'section'
    || (clipMode === 'field-plane'
      && maskMatchesGeometry(maskState, fieldJobId, fieldGeometrySha256)
      && maskState.watertight === true);

  useEffect(() => installViewportTestHook(scheduler), [scheduler]);
  useEffect(() => () => scheduler.dispose(), [scheduler]);
  useRepaintWhenShown(scheduler);
  useLayoutEffect(() => {
    if (!clipDefinition) return;
    stableClipPlane.copy(clipDefinition);
    scheduler.schedule();
  }, [clipDefinition, scheduler, stableClipPlane]);
  useEffect(() => {
    scheduler.schedule();
  }, [clipMode, invertFieldClip, mode, scheduler, showEnclosure]);
  useEffect(() => () => materials.all.forEach((material) => material.dispose()), [materials]);

  return <>
    {/* Studio lighting, warm key and cooler fill, but both inside the palette
        the surrounding interface uses. The fill used to be a saturated sky
        blue, which tinted the whole model towards a hue that appears nowhere
        else in the app and made the clay material read grey-blue rather than
        as the warm stone its token asks for. The key/fill contrast that gives
        the surface its form is kept -- it now comes from temperature within
        the warm range rather than from crossing into blue. */}
    <hemisphereLight args={theme === 'light' ? ['#fff8e8', '#6f6a60', 1.28] : ['#efe3d2', '#1b1917', 1.52]} />
    <directionalLight
      color={theme === 'light' ? '#fff0d1' : '#fdf6ec'}
      intensity={theme === 'light' ? 2.25 : 2.35}
      position={[size.x || 100, (size.y || 100) * 1.15, size.z || 100]}
    />
    <directionalLight
      color={theme === 'light' ? '#b0a08c' : '#c2ae97'}
      intensity={theme === 'light' ? 0.92 : 1.05}
      position={[-(size.x || 100), (size.y || 100) * 0.2, size.z || 100]}
    />
    <FieldPlane
      unitsPerMetre={scene.unitsPerMetre}
      clipPlane={clipMode === 'section' ? stableClipPlane : null}
      colormap={fieldColormap}
      scheduler={scheduler}
    />
    {scene.surfaces.map((surface) => <SurfaceMesh
      key={surface.key}
      surface={surface}
      mode={mode}
      visible={showEnclosure || !surface.enclosure}
      sectionCut={clipActive}
      materials={materials}
      scheduler={scheduler}
    />)}
    {capQuad && capTrusted && <mesh
      matrix={capQuad.matrix}
      matrixAutoUpdate={false}
      material={materials.cap}
      renderOrder={999}
    >
      <planeGeometry args={[1, 1]} />
    </mesh>}
    <CameraRig bounds={scene.bounds} request={cameraRequest} zoomRequest={zoomRequest} projection={cameraProjection} preferences={preferences} scheduler={scheduler} controls={controls} />
    <CameraGizmo controls={controls} center={center} scheduler={scheduler} theme={theme} onDirection={onCameraDirection} />
    <GizmoAxisPicker onDirection={onCameraDirection} />
    <PaintObserver marker={sceneMarker} startedAt={frameStartedAt} onClientFrame={onClientFrame} />
  </>;
}

class CanvasErrorBoundary extends Component<{ children: ReactNode; onError: (message: string) => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, _info: ErrorInfo) { this.props.onError(error.message || 'WebGL renderer failed'); }
  render() { return this.state.failed ? null : this.props.children; }
}

/** Whether the drawing buffer still needs to catch up with its container. */
export function canvasNeedsRemeasure(
  container: Pick<HTMLElement, 'clientWidth' | 'clientHeight'>,
  canvas: Pick<HTMLElement, 'clientWidth' | 'clientHeight'> | null,
): boolean {
  if (!canvas || !container.clientWidth || !container.clientHeight) return false;
  return Math.abs(canvas.clientWidth - container.clientWidth) > 1
    || Math.abs(canvas.clientHeight - container.clientHeight) > 1;
}

/**
 * Force react-three-fiber to re-measure when its own observer missed the mount.
 *
 * On a cold start — no stored dock layout — the panel is sized after the React
 * root inside it mounts, and r3f's measurement never fires again: the canvas
 * stays at the HTML default 300x150 inside a full-size panel and the viewport
 * renders nothing at all. Warm reloads hid this completely, because a restored
 * layout sizes the panel before the canvas mounts. A window resize is the event
 * r3f's measurement hook already listens for, and the size comparison keeps this
 * to the one nudge it takes rather than a loop.
 */
function useCanvasRemeasure(host: RefObject<HTMLDivElement | null>): void {
  useEffect(() => {
    const container = host.current;
    if (!container || typeof ResizeObserver === 'undefined') return undefined;
    const sync = () => {
      if (canvasNeedsRemeasure(container, container.querySelector('canvas'))) {
        window.dispatchEvent(new Event('resize'));
      }
    };
    const observer = new ResizeObserver(sync);
    observer.observe(container);
    sync();
    return () => observer.disconnect();
  }, [host]);
}

export const ViewportCanvas = memo(function ViewportCanvas({ onRenderFailure, ...props }: ViewportCanvasProps) {
  const host = useRef<HTMLDivElement>(null);
  const detachContextLoss = useRef<(() => void) | null>(null);
  useCanvasRemeasure(host);
  // Drop the context-loss listener along with the canvas that owns it, so React
  // Three Fiber's deferred `forceContextLoss()` during disposal cannot be
  // mistaken for a fault. See installContextLossFallback.
  useEffect(() => () => {
    detachContextLoss.current?.();
    detachContextLoss.current = null;
  }, []);
  return <CanvasErrorBoundary onError={onRenderFailure}><div ref={host} className="wg2-viewport-canvas-host"><Canvas
    className="wg2-viewport-canvas"
    frameloop="demand"
    camera={{ fov: 34, near: 0.01, far: 100_000 }}
    gl={{ antialias: true, alpha: true, stencil: true }}
    onCreated={({ gl }) => {
      gl.localClippingEnabled = true;
      // The canvas is a tab stop because OrbitControls needs focus to take key
      // input -- but it was an unnamed one, so a screen reader announced an
      // empty stop with no way to know what it held or that it was operable.
      gl.domElement.tabIndex = 0;
      gl.domElement.setAttribute('role', 'application');
      gl.domElement.setAttribute('aria-label', 'Waveguide geometry preview. Drag to orbit, scroll to zoom; arrow keys pan while focused.');
      gl.domElement.addEventListener('pointerdown', () => gl.domElement.focus());
      detachContextLoss.current?.();
      detachContextLoss.current = installContextLossFallback(gl.domElement, onRenderFailure);
    }}
  >
    <Scene {...props} />
  </Canvas></div></CanvasErrorBoundary>;
});
