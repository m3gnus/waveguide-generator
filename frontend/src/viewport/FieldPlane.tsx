import { useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import {
  ClampToEdgeWrapping,
  DataTexture,
  DoubleSide,
  FloatType,
  LinearFilter,
  Matrix4,
  RedFormat,
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
import { buildLutRgba, FIELD_PLANE_WINDOW_DB, maxFieldSplDb } from './fieldPlaneColor';
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

function floatTexture(data: Float32Array, width: number, height: number): DataTexture {
  const texture = new DataTexture(data, width, height, RedFormat, FloatType);
  texture.internalFormat = 'R32F';
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

export function fieldPlaneTransform(plane: FieldPlaneSpec, unitsPerMetre: number): Matrix4 {
  const axisU = new Vector3(...plane.axis_u);
  const axisV = new Vector3(...plane.axis_v);
  const normal = axisU.clone().cross(axisV);
  const transform = new Matrix4().makeBasis(axisU, axisV, normal);
  transform.setPosition(new Vector3(...plane.origin_m).multiplyScalar(unitsPerMetre));
  return transform;
}

function ReadyFieldPlane({ plane, field, unitsPerMetre, clipPlane, colormap, scheduler }: ReadyFieldPlaneProps) {
  const textures = useMemo(() => ({
    real: floatTexture(field.real, field.header.nx, field.header.ny),
    imag: floatTexture(field.imag, field.header.nx, field.header.ny),
    lut: lutTexture(colormap),
  }), [colormap, field]);
  const maxDb = useMemo(() => maxFieldSplDb(field.real, field.imag), [field]);
  const material = useMemo(() => new ShaderMaterial({
    clipping: clipPlane !== null,
    clippingPlanes: clipPlane ? [clipPlane] : [],
    depthWrite: false,
    depthTest: true,
    side: DoubleSide,
    transparent: true,
    toneMapped: false,
    uniforms: {
      uFieldReal: { value: textures.real },
      uFieldImag: { value: textures.imag },
      uColorLut: { value: textures.lut },
      uWindowMinDb: { value: maxDb - FIELD_PLANE_WINDOW_DB },
      uWindowMaxDb: { value: maxDb },
      uOpacity: { value: 0.92 },
    },
    vertexShader: FIELD_PLANE_VERTEX_SHADER,
    fragmentShader: FIELD_PLANE_FRAGMENT_SHADER,
  }), [clipPlane, maxDb, textures]);
  const transform = useMemo(() => fieldPlaneTransform(plane, unitsPerMetre), [plane, unitsPerMetre]);

  useEffect(() => {
    scheduler.schedule();
  }, [material, scheduler, transform]);
  useEffect(() => () => {
    textures.real.dispose();
    textures.imag.dispose();
    textures.lut.dispose();
  }, [textures]);
  useEffect(() => () => material.dispose(), [material]);

  return <mesh matrix={transform} matrixAutoUpdate={false} material={material} renderOrder={1_000}>
    <planeGeometry args={[plane.width_m * unitsPerMetre, plane.height_m * unitsPerMetre]}/>
  </mesh>;
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
  if (!enabled || !plane) return null;
  return <>
    {field && <ReadyFieldPlane {...props} plane={plane} field={field}/>}
    <FieldPlaneHandles plane={plane} unitsPerMetre={props.unitsPerMetre} scheduler={props.scheduler}/>
  </>;
}
