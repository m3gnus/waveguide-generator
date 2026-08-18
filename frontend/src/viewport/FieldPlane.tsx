import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import {
  ClampToEdgeWrapping,
  DataTexture,
  DoubleSide,
  FloatType,
  LinearFilter,
  Matrix4,
  NearestFilter,
  RedFormat,
  RGFormat,
  RGBAFormat,
  Raycaster,
  ShaderMaterial,
  UnsignedByteType,
  Vector2,
  Vector3,
  type Group,
  type Object3D,
  type Plane,
} from 'three';
import type { DecodedFieldPlane, FieldPlaneSpec } from '../api/fieldPlane';
import {
  advanceFieldPlanePhase,
  buildLutRgba,
  defaultFieldPlaneWindow,
  fieldPlaneColormap,
  FIELD_PLANE_MODE_UNIFORM,
  maxFieldSplDb,
} from './fieldPlaneColor';
import {
  fieldPlaneNormal,
  rotateFieldPlane,
  rotationAngleFromRays,
  snapFieldPlaneRotation,
  translationDeltaAlongNormal,
  type FieldPlaneRay,
  type FieldPlaneRotationAxis,
} from './fieldPlaneMath';
import { FIELD_PLANE_FRAGMENT_SHADER, FIELD_PLANE_VERTEX_SHADER } from './fieldPlaneShader';
import type { FieldPlaneMaskRequest, FieldPlaneMaskResponse } from './fieldPlaneMaskProtocol';
import { maskMatchesGeometry, useFieldPlaneMaskStore, type AppliedFieldPlaneMask } from './fieldPlaneMaskStore';
import { useFieldPlaneStore } from './fieldPlaneStore';
import type { DemandRenderScheduler } from './demandRender';

interface FieldPlaneProps {
  unitsPerMetre: number;
  clipPlane: Plane | null;
  colormap: readonly string[];
  scheduler: DemandRenderScheduler;
}

interface ReadyFieldPlaneProps extends FieldPlaneProps {
  plane: FieldPlaneSpec;
  field: DecodedFieldPlane;
}

type FieldPlaneHandle = 'translate' | 'rotate-u' | 'rotate-v';

interface ActiveFieldPlaneDrag {
  pointerId: number;
  handle: FieldPlaneHandle;
  startPlane: FieldPlaneSpec;
  startRay: FieldPlaneRay;
}

function complexTexture(real: Float32Array, imag: Float32Array, width: number, height: number): DataTexture {
  if (real.length !== imag.length) throw new Error('Complex field components must have equal lengths');
  const complex = new Float32Array(real.length * 2);
  for (let index = 0; index < real.length; index += 1) {
    complex[index * 2] = real[index];
    complex[index * 2 + 1] = imag[index];
  }
  const texture = new DataTexture(complex, width, height, RGFormat, FloatType);
  texture.internalFormat = 'RG32F';
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.wrapS = ClampToEdgeWrapping;
  texture.wrapT = ClampToEdgeWrapping;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

function lutTexture(colormap: readonly string[]): DataTexture {
  const data = buildLutRgba(colormap);
  const texture = new DataTexture(data, data.length / 4, 1, RGBAFormat, UnsignedByteType);
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.wrapS = ClampToEdgeWrapping;
  texture.wrapT = ClampToEdgeWrapping;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

function maskTexture(mask: AppliedFieldPlaneMask | null): DataTexture {
  const texture = new DataTexture(
    mask?.data ?? new Uint8Array([0]),
    mask?.nx ?? 1,
    mask?.ny ?? 1,
    RedFormat,
    UnsignedByteType,
  );
  texture.internalFormat = 'R8';
  texture.minFilter = NearestFilter;
  texture.magFilter = NearestFilter;
  texture.wrapS = ClampToEdgeWrapping;
  texture.wrapT = ClampToEdgeWrapping;
  texture.generateMipmaps = false;
  texture.unpackAlignment = 1;
  texture.needsUpdate = true;
  return texture;
}

export function fieldPlaneTransform(plane: FieldPlaneSpec, unitsPerMetre: number): Matrix4 {
  const axisU = new Vector3(...plane.axis_u);
  const axisV = new Vector3(...plane.axis_v);
  const normal = axisU.clone().cross(axisV);
  const transform = new Matrix4().makeBasis(axisU, axisV, normal);
  transform.setPosition(new Vector3(...plane.origin_m).multiplyScalar(unitsPerMetre));
  return transform;
}

export function fieldPlaneAnimationFrame(
  animating: boolean,
  currentPhase: number,
  deltaSeconds: number,
  cyclesPerSecond: number,
  scheduler: Pick<DemandRenderScheduler, 'schedule'>,
): number {
  if (!animating) return 0;
  const next = advanceFieldPlanePhase(currentPhase, deltaSeconds, cyclesPerSecond);
  scheduler.schedule();
  return next;
}

function ReadyFieldPlane({ plane, field, unitsPerMetre, clipPlane, colormap, scheduler }: ReadyFieldPlaneProps) {
  const displayMode = useFieldPlaneStore((state) => state.displayMode);
  const rangeWindow = useFieldPlaneStore((state) => state.windows[state.displayMode]);
  const animating = useFieldPlaneStore((state) => state.animating);
  const animationSpeed = useFieldPlaneStore((state) => state.animationSpeed);
  const isolines = useFieldPlaneStore((state) => state.isolines);
  const fieldTexture = useMemo(
    () => complexTexture(field.real, field.imag, field.header.nx, field.header.ny),
    [field],
  );
  const activeColormap = useMemo(() => fieldPlaneColormap(displayMode, colormap), [colormap, displayMode]);
  const colorTexture = useMemo(() => lutTexture(activeColormap), [activeColormap]);
  const maskState = useFieldPlaneMaskStore((state) => state);
  const appliedMask = maskMatchesGeometry(maskState, field.header.job_id, field.header.geometry_sha256)
    ? maskState.mask
    : null;
  const alphaTexture = useMemo(() => maskTexture(appliedMask), [appliedMask]);
  const maxDb = useMemo(() => maxFieldSplDb(field.real, field.imag), [field]);
  const window = rangeWindow ?? defaultFieldPlaneWindow(displayMode);
  const material = useMemo(() => new ShaderMaterial({
    clipping: clipPlane !== null,
    clippingPlanes: clipPlane ? [clipPlane] : [],
    depthWrite: false,
    depthTest: true,
    side: DoubleSide,
    transparent: true,
    toneMapped: false,
    uniforms: {
      uFieldComplex: { value: fieldTexture },
      uColorLut: { value: colorTexture },
      uMask: { value: alphaTexture },
      uDisplayMode: { value: FIELD_PLANE_MODE_UNIFORM[displayMode] },
      uFieldMaxDb: { value: maxDb },
      uWindowMin: { value: window.minimum },
      uWindowMax: { value: window.maximum },
      uTimePhase: { value: 0 },
      uIsolines: { value: isolines ? 1 : 0 },
      uOpacity: { value: 0.92 },
    },
    vertexShader: FIELD_PLANE_VERTEX_SHADER,
    fragmentShader: FIELD_PLANE_FRAGMENT_SHADER,
  }), [clipPlane, colorTexture, fieldTexture, maxDb]);
  const transform = useMemo(() => fieldPlaneTransform(plane, unitsPerMetre), [plane, unitsPerMetre]);

  useFrame((_state, delta) => {
    material.uniforms.uTimePhase.value = fieldPlaneAnimationFrame(
      animating,
      material.uniforms.uTimePhase.value as number,
      delta,
      animationSpeed,
      scheduler,
    );
  });

  useEffect(() => {
    material.uniforms.uMask.value = alphaTexture;
    scheduler.schedule();
  }, [alphaTexture, material, scheduler]);
  useEffect(() => {
    material.uniforms.uDisplayMode.value = FIELD_PLANE_MODE_UNIFORM[displayMode];
    material.uniforms.uWindowMin.value = window.minimum;
    material.uniforms.uWindowMax.value = window.maximum;
    material.uniforms.uIsolines.value = isolines ? 1 : 0;
    if (!animating) material.uniforms.uTimePhase.value = 0;
    scheduler.schedule();
  }, [animating, displayMode, isolines, material, scheduler, window.maximum, window.minimum]);
  useEffect(() => {
    scheduler.schedule();
  }, [material, scheduler, transform]);
  useEffect(() => () => fieldTexture.dispose(), [fieldTexture]);
  useEffect(() => () => colorTexture.dispose(), [colorTexture]);
  useEffect(() => () => alphaTexture.dispose(), [alphaTexture]);
  useEffect(() => () => material.dispose(), [material]);

  return <mesh matrix={transform} matrixAutoUpdate={false} material={material} renderOrder={1_000}>
    <planeGeometry args={[plane.width_m * unitsPerMetre, plane.height_m * unitsPerMetre]}/>
  </mesh>;
}

function FieldPlaneMaskController({ scheduler }: Pick<FieldPlaneProps, 'scheduler'>) {
  const enabled = useFieldPlaneStore((state) => state.enabled);
  const dragging = useFieldPlaneStore((state) => state.dragging);
  const jobId = useFieldPlaneStore((state) => state.jobId);
  const geometrySha256 = useFieldPlaneStore((state) => state.geometrySha256);
  const plane = useFieldPlaneStore((state) => state.plane);
  const worker = useRef<Worker | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    if (!enabled) {
      useFieldPlaneMaskStore.getState().clear();
      return undefined;
    }
    const instance = new Worker(new URL('./fieldPlaneMask.worker.ts', import.meta.url), { type: 'module' });
    const onMessage = (event: MessageEvent<FieldPlaneMaskResponse>) => {
      if (event.data.type === 'result') useFieldPlaneMaskStore.getState().apply(event.data);
      else useFieldPlaneMaskStore.getState().fail(event.data);
      scheduler.schedule();
    };
    instance.addEventListener('message', onMessage);
    worker.current = instance;
    return () => {
      worker.current = null;
      instance.removeEventListener('message', onMessage);
      instance.terminate();
      useFieldPlaneMaskStore.getState().clear();
    };
  }, [enabled, scheduler]);

  useEffect(() => {
    if (!enabled || dragging || !jobId || !geometrySha256 || !plane || !worker.current) return;
    generation.current += 1;
    const request: FieldPlaneMaskRequest = {
      type: 'classify',
      generation: generation.current,
      jobId,
      geometrySha256,
      plane,
    };
    useFieldPlaneMaskStore.getState().begin(request);
    worker.current.postMessage(request);
  }, [dragging, enabled, geometrySha256, jobId, plane]);

  return null;
}

function rayFromPointer(
  event: PointerEvent,
  element: HTMLCanvasElement,
  camera: Parameters<Raycaster['setFromCamera']>[1],
  raycaster: Raycaster,
): FieldPlaneRay {
  const bounds = element.getBoundingClientRect();
  const pointer = new Vector2(
    ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
  );
  raycaster.setFromCamera(pointer, camera);
  return {
    origin: raycaster.ray.origin.toArray(),
    direction: raycaster.ray.direction.toArray(),
  };
}

function nearestHandle(
  raycaster: Raycaster,
  candidates: ReadonlyArray<[FieldPlaneHandle, Object3D | null]>,
): FieldPlaneHandle | null {
  let nearest: FieldPlaneHandle | null = null;
  let nearestDistance = Infinity;
  for (const [handle, object] of candidates) {
    if (!object) continue;
    const hit = raycaster.intersectObject(object, true)[0];
    if (hit && hit.distance < nearestDistance) {
      nearest = handle;
      nearestDistance = hit.distance;
    }
  }
  return nearest;
}

function FieldPlaneHandles({ plane, unitsPerMetre, scheduler }: Pick<ReadyFieldPlaneProps, 'plane' | 'unitsPerMetre' | 'scheduler'>) {
  const gl = useThree((state) => state.gl);
  const camera = useThree((state) => state.camera);
  const translateRef = useRef<Group>(null);
  const rotateURef = useRef<Group>(null);
  const rotateVRef = useRef<Group>(null);
  const drag = useRef<ActiveFieldPlaneDrag | null>(null);
  const transform = useMemo(() => fieldPlaneTransform(plane, unitsPerMetre), [plane, unitsPerMetre]);
  const smallerExtent = Math.max(Math.min(plane.width_m, plane.height_m) * unitsPerMetre, 1e-6);
  const handleLength = smallerExtent * 0.34;
  const ringRadius = smallerExtent * 0.24;
  const handleRadius = smallerExtent * 0.009;
  const pickRadius = smallerExtent * 0.035;

  useEffect(() => {
    const element = gl.domElement;
    const raycaster = new Raycaster();
    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || drag.current) return;
      const startRay = rayFromPointer(event, element, camera, raycaster);
      const handle = nearestHandle(raycaster, [
        ['translate', translateRef.current],
        ['rotate-u', rotateURef.current],
        ['rotate-v', rotateVRef.current],
      ]);
      if (!handle) return;
      // Claim the press before OrbitControls sees the gesture.
      event.preventDefault();
      event.stopPropagation();
      element.setPointerCapture(event.pointerId);
      drag.current = {
        pointerId: event.pointerId,
        handle,
        startPlane: useFieldPlaneStore.getState().plane ?? plane,
        startRay,
      };
      useFieldPlaneStore.getState().beginPlaneDrag();
    };
    const onPointerMove = (event: PointerEvent) => {
      const active = drag.current;
      if (!active || active.pointerId !== event.pointerId) return;
      event.preventDefault();
      event.stopPropagation();
      const currentRay = rayFromPointer(event, element, camera, raycaster);
      let next: FieldPlaneSpec | null = null;
      if (active.handle === 'translate') {
        const normal = fieldPlaneNormal(active.startPlane);
        const origin = active.startPlane.origin_m.map((value) => value * unitsPerMetre) as [number, number, number];
        const delta = translationDeltaAlongNormal(active.startRay, currentRay, origin, normal);
        if (delta !== null) {
          next = {
            ...active.startPlane,
            origin_m: active.startPlane.origin_m.map((value, index) => (
              value + normal[index] * delta / unitsPerMetre
            )) as [number, number, number],
          };
        }
      } else {
        const rotationAxis: FieldPlaneRotationAxis = active.handle === 'rotate-u' ? 'u' : 'v';
        const center = active.startPlane.origin_m.map((value) => value * unitsPerMetre) as [number, number, number];
        const axis = rotationAxis === 'u' ? active.startPlane.axis_u : active.startPlane.axis_v;
        const angle = rotationAngleFromRays(active.startRay, currentRay, center, axis);
        if (angle !== null) next = rotateFieldPlane(active.startPlane, rotationAxis, snapFieldPlaneRotation(angle, event.altKey));
      }
      if (!next) return;
      useFieldPlaneStore.getState().updatePlaneDrag(next);
      scheduler.schedule();
    };
    const finishDrag = (event: PointerEvent) => {
      const active = drag.current;
      if (!active || active.pointerId !== event.pointerId) return;
      onPointerMove(event);
      event.preventDefault();
      event.stopPropagation();
      drag.current = null;
      if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
      useFieldPlaneStore.getState().endPlaneDrag();
      scheduler.schedule();
    };
    element.addEventListener('pointerdown', onPointerDown, { capture: true });
    element.addEventListener('pointermove', onPointerMove, { capture: true });
    element.addEventListener('pointerup', finishDrag, { capture: true });
    element.addEventListener('pointercancel', finishDrag, { capture: true });
    return () => {
      element.removeEventListener('pointerdown', onPointerDown, { capture: true });
      element.removeEventListener('pointermove', onPointerMove, { capture: true });
      element.removeEventListener('pointerup', finishDrag, { capture: true });
      element.removeEventListener('pointercancel', finishDrag, { capture: true });
    };
  }, [camera, gl, plane, scheduler, unitsPerMetre]);

  return <group matrix={transform} matrixAutoUpdate={false} renderOrder={1_100}>
    <group ref={translateRef}>
      <mesh position={[0, 0, handleLength / 2]} rotation={[Math.PI / 2, 0, 0]} renderOrder={1_100}>
        <cylinderGeometry args={[handleRadius, handleRadius, handleLength, 12]}/>
        <meshBasicMaterial color="#f0a45d" transparent opacity={0.78} depthTest={false}/>
      </mesh>
      <mesh position={[0, 0, handleLength]} rotation={[Math.PI / 2, 0, 0]} renderOrder={1_100}>
        <coneGeometry args={[handleRadius * 3, handleRadius * 7, 16]}/>
        <meshBasicMaterial color="#f0a45d" transparent opacity={0.88} depthTest={false}/>
      </mesh>
      <mesh position={[0, 0, handleLength / 2]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[pickRadius, pickRadius, handleLength * 1.3, 10]}/>
        <meshBasicMaterial transparent opacity={0} depthWrite={false}/>
      </mesh>
    </group>
    <group ref={rotateURef} rotation={[0, Math.PI / 2, 0]}>
      <mesh renderOrder={1_100}>
        <torusGeometry args={[ringRadius, handleRadius, 8, 48]}/>
        <meshBasicMaterial color="#7ea8df" transparent opacity={0.68} depthTest={false}/>
      </mesh>
      <mesh>
        <torusGeometry args={[ringRadius, pickRadius, 8, 48]}/>
        <meshBasicMaterial transparent opacity={0} depthWrite={false}/>
      </mesh>
    </group>
    <group ref={rotateVRef} rotation={[Math.PI / 2, 0, 0]}>
      <mesh renderOrder={1_100}>
        <torusGeometry args={[ringRadius, handleRadius, 8, 48]}/>
        <meshBasicMaterial color="#83bd90" transparent opacity={0.68} depthTest={false}/>
      </mesh>
      <mesh>
        <torusGeometry args={[ringRadius, pickRadius, 8, 48]}/>
        <meshBasicMaterial transparent opacity={0} depthWrite={false}/>
      </mesh>
    </group>
  </group>;
}

export function FieldPlane(props: FieldPlaneProps) {
  const enabled = useFieldPlaneStore((state) => state.enabled);
  const plane = useFieldPlaneStore((state) => state.plane);
  const field = useFieldPlaneStore((state) => state.field);
  useEffect(() => {
    const state = useFieldPlaneStore.getState();
    if (state.enabled) state.resume();
    return () => useFieldPlaneStore.getState().cancelPending();
  }, []);
  return <>
    <FieldPlaneMaskController scheduler={props.scheduler}/>
    {enabled && plane && <>
      {field && <ReadyFieldPlane {...props} plane={plane} field={field}/>}
      <FieldPlaneHandles plane={plane} unitsPerMetre={props.unitsPerMetre} scheduler={props.scheduler}/>
    </>}
  </>;
}
