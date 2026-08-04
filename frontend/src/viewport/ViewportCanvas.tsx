import { OrbitControls } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Component, useEffect, useLayoutEffect, useMemo, useRef, type ComponentRef, type ErrorInfo, type ReactNode } from 'react';
import { OrthographicCamera, PerspectiveCamera, Plane, Vector3 } from 'three';
import type { CameraProjection, ViewerPreferences } from '../viewerprefs/viewerPreferences';
import { calculateCameraFit, zoomedOrthographicValue } from './cameraMath';
import { DemandRenderScheduler, installViewportTestHook } from './demandRender';
import type { FrameScene } from './frameScene';
import { createMaterialLibrary } from './materials';
import { SurfaceMesh } from './SurfaceMesh';
import type { CameraPreset, DisplayMode, ViewportTheme } from './types';

export interface CameraRequest {
  preset: CameraPreset;
  nonce: number;
}

export interface ZoomRequest {
  direction: 'in' | 'out';
  nonce: number;
}

interface ViewportCanvasProps {
  scene: FrameScene;
  sceneMarker: string;
  mode: DisplayMode;
  showEnclosure: boolean;
  sectionCut: boolean;
  cameraRequest: CameraRequest;
  zoomRequest: ZoomRequest;
  cameraProjection: CameraProjection;
  preferences: ViewerPreferences;
  frameStartedAt: number | null;
  onClientFrame: (milliseconds: number) => void;
  theme: ViewportTheme;
  onRenderFailure: (message: string) => void;
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

export function installContextLossFallback(canvas: HTMLCanvasElement, onFailure: (message: string) => void): void {
  canvas.addEventListener('webglcontextlost', (event) => {
    event.preventDefault();
    onFailure('WebGL2 context was lost');
  }, { once: true });
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

function CameraRig({ bounds, request, zoomRequest, projection, preferences, scheduler }: {
  bounds: FrameScene['bounds'];
  request: CameraRequest;
  zoomRequest: ZoomRequest;
  projection: CameraProjection;
  preferences: ViewerPreferences;
  scheduler: DemandRenderScheduler;
}) {
  const set = useThree((state) => state.set);
  const get = useThree((state) => state.get);
  const canvasSize = useThree((state) => state.size);
  const gl = useThree((state) => state.gl);
  const aspect = Math.max(canvasSize.width / Math.max(canvasSize.height, 1), 0.01);
  const camera = useMemo<PerspectiveCamera | OrthographicCamera>(() => projection === 'orthographic'
    ? new OrthographicCamera(-1, 1, 1, -1, 0.001, 100_000)
    : new PerspectiveCamera(34, aspect, 0.001, 100_000), [aspect, projection]);
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null);
  const center = useMemo(() => bounds.getCenter(new Vector3()), [bounds]);
  const appliedRequest = useRef<string | null>(null);
  const appliedZoom = useRef(zoomRequest.nonce);
  const fitKey = cameraFitKey(bounds, request.nonce, projection, aspect);

  useLayoutEffect(() => {
    const previous = get().camera;
    set({ camera });
    return () => set({ camera: previous });
  }, [camera, get, set]);

  useEffect(() => {
    if (appliedRequest.current === fitKey) return;
    appliedRequest.current = fitKey;
    return scheduler.schedule(() => {
      const fit = calculateCameraFit(bounds, request.preset, projection, aspect);
      camera.position.copy(fit.position);
      camera.up.set(0, request.preset === 'top' ? 0 : 1, request.preset === 'top' ? -1 : 0);
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
      controls.current?.target.copy(fit.center);
      controls.current?.update();
    });
  }, [aspect, bounds, camera, fitKey, projection, request.preset, scheduler]);

  useEffect(() => {
    if (appliedZoom.current === zoomRequest.nonce) return;
    appliedZoom.current = zoomRequest.nonce;
    return scheduler.schedule(() => {
      const target = controls.current?.target ?? center;
      if (camera instanceof OrthographicCamera) {
        camera.zoom = zoomedOrthographicValue(camera.zoom, zoomRequest.direction);
        camera.updateProjectionMatrix();
      } else {
        const factor = zoomRequest.direction === 'in' ? 0.8 : 1.25;
        camera.position.sub(target).multiplyScalar(factor).add(target);
      }
      controls.current?.update();
    });
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

  return <OrbitControls
    ref={controls}
    camera={camera}
    makeDefault
    target={[center.x, center.y, center.z]}
    rotateSpeed={preferences.rotateSpeed}
    zoomSpeed={preferences.zoomSpeed}
    panSpeed={preferences.panSpeed}
    enableDamping={preferences.dampingEnabled}
    dampingFactor={preferences.dampingFactor}
    zoomToCursor
    onChange={() => scheduler.schedule()}
  />;
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

function Scene({ scene, sceneMarker, mode, showEnclosure, sectionCut, cameraRequest, zoomRequest, cameraProjection, preferences, frameStartedAt, onClientFrame, theme }: Omit<ViewportCanvasProps, 'onRenderFailure'>) {
  const invalidate = useThree((state) => state.invalidate);
  const scheduler = useMemo(() => new DemandRenderScheduler(invalidate), [invalidate]);
  const clipPlane = useMemo(() => sectionCut ? new Plane(new Vector3(1, 0, 0), 0) : null, [sectionCut]);
  const materials = useMemo(() => createMaterialLibrary(mode, clipPlane, theme), [clipPlane, mode, theme]);
  const center = useMemo(() => scene.bounds.getCenter(new Vector3()), [scene.bounds]);
  const size = useMemo(() => scene.bounds.getSize(new Vector3()), [scene.bounds]);

  useEffect(() => installViewportTestHook(scheduler), [scheduler]);
  useEffect(() => () => scheduler.dispose(), [scheduler]);
  useEffect(() => {
    scheduler.schedule();
  }, [mode, scheduler, sectionCut, showEnclosure]);
  useEffect(() => () => materials.all.forEach((material) => material.dispose()), [materials]);

  return <>
    <hemisphereLight args={theme === 'light' ? ['#fff8e8', '#697582', 1.28] : ['#c4e2f2', '#111925', 1.52]} />
    <directionalLight
      color={theme === 'light' ? '#fff0d1' : '#f1f9ff'}
      intensity={theme === 'light' ? 2.25 : 2.35}
      position={[size.x || 100, (size.y || 100) * 1.15, size.z || 100]}
    />
    <directionalLight
      color={theme === 'light' ? '#79a8ce' : '#79c6ee'}
      intensity={theme === 'light' ? 0.92 : 1.05}
      position={[-(size.x || 100), (size.y || 100) * 0.2, size.z || 100]}
    />
    {scene.surfaces.map((surface) => <SurfaceMesh
      key={surface.key}
      surface={surface}
      mode={mode}
      visible={showEnclosure || !surface.enclosure}
      sectionCut={sectionCut}
      materials={materials}
      scheduler={scheduler}
    />)}
    {sectionCut && <mesh
      position={[0, center.y, center.z]}
      rotation={[0, Math.PI / 2, 0]}
      material={materials.cap}
      renderOrder={999}
    >
      <planeGeometry args={[Math.max(size.z * 1.05, 1), Math.max(size.y * 1.05, 1)]} />
    </mesh>}
    <CameraRig bounds={scene.bounds} request={cameraRequest} zoomRequest={zoomRequest} projection={cameraProjection} preferences={preferences} scheduler={scheduler} />
    <PaintObserver marker={sceneMarker} startedAt={frameStartedAt} onClientFrame={onClientFrame} />
  </>;
}

class CanvasErrorBoundary extends Component<{ children: ReactNode; onError: (message: string) => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, _info: ErrorInfo) { this.props.onError(error.message || 'WebGL renderer failed'); }
  render() { return this.state.failed ? null : this.props.children; }
}

export function ViewportCanvas({ onRenderFailure, ...props }: ViewportCanvasProps) {
  return <CanvasErrorBoundary onError={onRenderFailure}><Canvas
    className="wg2-viewport-canvas"
    frameloop="demand"
    camera={{ fov: 34, near: 0.01, far: 100_000 }}
    gl={{ antialias: true, alpha: true, stencil: true }}
    onCreated={({ gl }) => {
      gl.localClippingEnabled = true;
      gl.domElement.tabIndex = 0;
      gl.domElement.addEventListener('pointerdown', () => gl.domElement.focus());
      installContextLossFallback(gl.domElement, onRenderFailure);
    }}
  >
    <Scene {...props} />
  </Canvas></CanvasErrorBoundary>;
}
