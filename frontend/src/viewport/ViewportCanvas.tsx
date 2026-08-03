import { OrbitControls } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import { Box3, PerspectiveCamera, Plane, Vector3 } from 'three';
import type { DecodedFrame } from '../api/frame';
import { DemandRenderScheduler, installViewportTestHook } from './demandRender';
import { frameToScene } from './frameScene';
import { createMaterialLibrary } from './materials';
import { SurfaceMesh } from './SurfaceMesh';
import type { CameraPreset, DisplayMode, ViewportTheme } from './types';

export interface CameraRequest {
  preset: CameraPreset;
  nonce: number;
}

interface ViewportCanvasProps {
  frame: DecodedFrame;
  mode: DisplayMode;
  showEnclosure: boolean;
  sectionCut: boolean;
  cameraRequest: CameraRequest;
  frameStartedAt: number | null;
  onClientFrame: (milliseconds: number) => void;
  theme: ViewportTheme;
}

export function canRenderWebGL(): boolean {
  return typeof WebGLRenderingContext !== 'undefined' || typeof WebGL2RenderingContext !== 'undefined';
}

function CameraRig({ bounds, request, scheduler }: {
  bounds: Box3;
  request: CameraRequest;
  scheduler: DemandRenderScheduler;
}) {
  const camera = useThree((state) => state.camera);
  const center = useMemo(() => bounds.getCenter(new Vector3()), [bounds]);
  const size = useMemo(() => Math.max(bounds.getSize(new Vector3()).length(), 1), [bounds]);
  const appliedRequest = useRef<number | null>(null);

  useEffect(() => {
    if (appliedRequest.current === request.nonce) return;
    appliedRequest.current = request.nonce;
    return scheduler.schedule(() => {
      const distance = size * 1.25;
      const offset = request.preset === 'front'
        ? new Vector3(0, 0, distance)
        : request.preset === 'top'
          ? new Vector3(0, distance, 0.001)
          : new Vector3(distance * 0.72, distance * 0.52, distance * 0.72);
      camera.position.copy(center).add(offset);
      camera.up.set(0, request.preset === 'top' ? 0 : 1, request.preset === 'top' ? -1 : 0);
      camera.lookAt(center);
      if (camera instanceof PerspectiveCamera) {
        camera.near = Math.max(0.01, size / 10_000);
        camera.far = Math.max(2_000, size * 100);
        camera.updateProjectionMatrix();
      }
    });
  }, [camera, center, request.nonce, request.preset, scheduler, size]);

  return <OrbitControls
    makeDefault
    target={[center.x, center.y, center.z]}
    enableDamping={false}
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

function Scene({ frame, mode, showEnclosure, sectionCut, cameraRequest, frameStartedAt, onClientFrame, theme }: ViewportCanvasProps) {
  const scene = useMemo(() => frameToScene(frame), [frame]);
  const invalidate = useThree((state) => state.invalidate);
  const scheduler = useMemo(() => new DemandRenderScheduler(invalidate), [invalidate]);
  const clipPlane = useMemo(() => sectionCut ? new Plane(new Vector3(1, 0, 0), 0) : null, [sectionCut]);
  const materials = useMemo(() => createMaterialLibrary(mode, clipPlane, theme), [clipPlane, mode, theme]);
  const center = useMemo(() => scene.bounds.getCenter(new Vector3()), [scene.bounds]);
  const size = useMemo(() => scene.bounds.getSize(new Vector3()), [scene.bounds]);
  const marker = `${frame.header.epoch ?? 0}:${frame.header.seq ?? 0}:${frame.header.designRevision ?? 0}:${frame.header.lod ?? ''}`;

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
    <CameraRig bounds={scene.bounds} request={cameraRequest} scheduler={scheduler} />
    <PaintObserver marker={marker} startedAt={frameStartedAt} onClientFrame={onClientFrame} />
  </>;
}

export function ViewportCanvas(props: ViewportCanvasProps) {
  return <Canvas
    className="wg2-viewport-canvas"
    frameloop="demand"
    camera={{ fov: 34, near: 0.01, far: 100_000 }}
    gl={{ antialias: true, alpha: true, stencil: true }}
    onCreated={({ gl }) => { gl.localClippingEnabled = true; }}
  >
    <Scene {...props} />
  </Canvas>;
}
