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
  fieldPlaneAxisVector,
  probeFieldPlaneRay,
  resizeFieldPlane,
  rotateFieldPlane,
  rotationAngleFromRays,
  snapFieldPlaneRotation,
  translateFieldPlane,
  translationDeltaAlongAxis,
  type FieldPlaneRay,
  type FieldPlaneRotationAxis,
  type FieldPlaneTranslationAxis,
} from './fieldPlaneMath';
import {
  fieldPlaneMaskedAt,
  sampleFieldPlaneBilinear,
  useFieldPlaneProbeStore,
} from './fieldPlaneProbe';
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

type FieldPlaneHandle =
  | 'translate-u'
  | 'translate-v'
  | 'translate-n'
  | 'rotate-u'
  | 'rotate-v'
  | 'resize-u'
  | 'resize-v'
  | 'resize-uv';

const TRANSLATION_AXIS: Readonly<Record<'translate-u' | 'translate-v' | 'translate-n', FieldPlaneTranslationAxis>> = {
  'translate-u': 'u',
  'translate-v': 'v',
  'translate-n': 'n',
};

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
  const frozenNormalizationDb = useFieldPlaneStore((state) => state.frozenNormalizationDb);
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
  const normalizationDb = frozenNormalizationDb ?? maxDb;
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
      uFieldMaxDb: { value: normalizationDb },
      uWindowMin: { value: window.minimum },
      uWindowMax: { value: window.maximum },
      uTimePhase: { value: 0 },
      uIsolines: { value: isolines ? 1 : 0 },
      uOpacity: { value: 0.92 },
    },
    vertexShader: FIELD_PLANE_VERTEX_SHADER,
    fragmentShader: FIELD_PLANE_FRAGMENT_SHADER,
  }), [clipPlane, colorTexture]);
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
    material.uniforms.uFieldComplex.value = fieldTexture;
    material.uniforms.uDisplayMode.value = FIELD_PLANE_MODE_UNIFORM[displayMode];
    material.uniforms.uFieldMaxDb.value = normalizationDb;
    material.uniforms.uWindowMin.value = window.minimum;
    material.uniforms.uWindowMax.value = window.maximum;
    material.uniforms.uIsolines.value = isolines ? 1 : 0;
    if (!animating) material.uniforms.uTimePhase.value = 0;
    scheduler.schedule();
  }, [
    animating,
    displayMode,
    fieldTexture,
    isolines,
    material,
    normalizationDb,
    scheduler,
    window.maximum,
    window.minimum,
  ]);
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
  const symmetryPlane = useFieldPlaneStore((state) => state.field?.header.symmetry_plane);
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
    if (!enabled || dragging || !jobId || !geometrySha256 || symmetryPlane === undefined || !plane || !worker.current) return;
    generation.current += 1;
    const request: FieldPlaneMaskRequest = {
      type: 'classify',
      generation: generation.current,
      jobId,
      geometrySha256,
      symmetryPlane,
      plane,
    };
    useFieldPlaneMaskStore.getState().begin(request);
    worker.current.postMessage(request);
  }, [dragging, enabled, geometrySha256, jobId, plane, symmetryPlane]);

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
  const translateURef = useRef<Group>(null);
  const translateVRef = useRef<Group>(null);
  const translateNRef = useRef<Group>(null);
  const rotateURef = useRef<Group>(null);
  const rotateVRef = useRef<Group>(null);
  const resizeURef = useRef<Group>(null);
  const resizeVRef = useRef<Group>(null);
  const resizeUVRef = useRef<Group>(null);
  const drag = useRef<ActiveFieldPlaneDrag | null>(null);
  const transform = useMemo(() => fieldPlaneTransform(plane, unitsPerMetre), [plane, unitsPerMetre]);
  const halfWidth = plane.width_m * unitsPerMetre / 2;
  const halfHeight = plane.height_m * unitsPerMetre / 2;
  const smallerExtent = Math.max(Math.min(plane.width_m, plane.height_m) * unitsPerMetre, 1e-6);
  const handleLength = smallerExtent * 0.34;
  const ringRadius = smallerExtent * 0.24;
  const handleRadius = smallerExtent * 0.009;
  const pickRadius = smallerExtent * 0.035;
  const gripSize = smallerExtent * 0.032;
  const gripPick = gripSize * 2.4;

  useEffect(() => {
    const element = gl.domElement;
    const raycaster = new Raycaster();
    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || drag.current) return;
      const startRay = rayFromPointer(event, element, camera, raycaster);
      const handle = nearestHandle(raycaster, [
        ['resize-uv', resizeUVRef.current],
        ['resize-u', resizeURef.current],
        ['resize-v', resizeVRef.current],
        ['translate-u', translateURef.current],
        ['translate-v', translateVRef.current],
        ['translate-n', translateNRef.current],
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
      const startPlane = active.startPlane;
      const center = startPlane.origin_m.map((value) => value * unitsPerMetre) as [number, number, number];
      const axisU = fieldPlaneAxisVector(startPlane, 'u');
      const axisV = fieldPlaneAxisVector(startPlane, 'v');
      const along = (axis: [number, number, number], gripOrigin: [number, number, number]) =>
        translationDeltaAlongAxis(active.startRay, currentRay, gripOrigin, axis);
      const offsetFrom = (
        uUnits: number,
        vUnits: number,
      ): [number, number, number] => [
        center[0] + axisU[0] * uUnits + axisV[0] * vUnits,
        center[1] + axisU[1] * uUnits + axisV[1] * vUnits,
        center[2] + axisU[2] * uUnits + axisV[2] * vUnits,
      ];
      const startHalfWidth = startPlane.width_m * unitsPerMetre / 2;
      const startHalfHeight = startPlane.height_m * unitsPerMetre / 2;
      let next: FieldPlaneSpec | null = null;
      if (active.handle === 'translate-u' || active.handle === 'translate-v' || active.handle === 'translate-n') {
        const axis = TRANSLATION_AXIS[active.handle];
        const delta = along(fieldPlaneAxisVector(startPlane, axis), center);
        if (delta !== null) next = translateFieldPlane(startPlane, axis, delta / unitsPerMetre);
      } else if (active.handle === 'rotate-u' || active.handle === 'rotate-v') {
        const rotationAxis: FieldPlaneRotationAxis = active.handle === 'rotate-u' ? 'u' : 'v';
        const axis = rotationAxis === 'u' ? startPlane.axis_u : startPlane.axis_v;
        const angle = rotationAngleFromRays(active.startRay, currentRay, center, axis);
        if (angle !== null) next = rotateFieldPlane(startPlane, rotationAxis, snapFieldPlaneRotation(angle, event.altKey));
      } else {
        // Grips sit on the +u / +v edges; the plane grows symmetrically about
        // its centre, so the extent changes by twice the handle travel.
        const grip = offsetFrom(
          active.handle === 'resize-v' ? 0 : startHalfWidth,
          active.handle === 'resize-u' ? 0 : startHalfHeight,
        );
        const widthDelta = active.handle === 'resize-v' ? 0 : along(axisU, grip);
        const heightDelta = active.handle === 'resize-u' ? 0 : along(axisV, grip);
        if (widthDelta !== null && heightDelta !== null) {
          next = resizeFieldPlane(
            startPlane,
            2 * widthDelta / unitsPerMetre,
            2 * heightDelta / unitsPerMetre,
          );
        }
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

  const arrow = (
    reference: typeof translateURef,
    color: string,
    rotation: [number, number, number],
  ) => <group ref={reference} rotation={rotation}>
    <mesh position={[0, 0, handleLength / 2]} rotation={[Math.PI / 2, 0, 0]} renderOrder={1_100}>
      <cylinderGeometry args={[handleRadius, handleRadius, handleLength, 12]}/>
      <meshBasicMaterial color={color} transparent opacity={0.78} depthTest={false}/>
    </mesh>
    <mesh position={[0, 0, handleLength]} rotation={[Math.PI / 2, 0, 0]} renderOrder={1_100}>
      <coneGeometry args={[handleRadius * 3, handleRadius * 7, 16]}/>
      <meshBasicMaterial color={color} transparent opacity={0.88} depthTest={false}/>
    </mesh>
    <mesh position={[0, 0, handleLength / 2]} rotation={[Math.PI / 2, 0, 0]}>
      <cylinderGeometry args={[pickRadius, pickRadius, handleLength * 1.3, 10]}/>
      <meshBasicMaterial transparent opacity={0} depthWrite={false}/>
    </mesh>
  </group>;

  const grip = (
    reference: typeof resizeURef,
    color: string,
    position: [number, number, number],
  ) => <group ref={reference} position={position}>
    <mesh renderOrder={1_100}>
      <boxGeometry args={[gripSize, gripSize, gripSize]}/>
      <meshBasicMaterial color={color} transparent opacity={0.82} depthTest={false}/>
    </mesh>
    <mesh>
      <boxGeometry args={[gripPick, gripPick, gripPick]}/>
      <meshBasicMaterial transparent opacity={0} depthWrite={false}/>
    </mesh>
  </group>;

  return <group matrix={transform} matrixAutoUpdate={false} renderOrder={1_100}>
    {/* Local +z is the plane normal, so each arrow group is rotated to point
        its shaft down the axis it drags along. */}
    {arrow(translateNRef, '#f0a45d', [0, 0, 0])}
    {arrow(translateURef, '#e4695e', [0, Math.PI / 2, 0])}
    {arrow(translateVRef, '#8fd07a', [-Math.PI / 2, 0, 0])}
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
        <meshBasicMaterial color="#b58ad6" transparent opacity={0.68} depthTest={false}/>
      </mesh>
      <mesh>
        <torusGeometry args={[ringRadius, pickRadius, 8, 48]}/>
        <meshBasicMaterial transparent opacity={0} depthWrite={false}/>
      </mesh>
    </group>
    {grip(resizeURef, '#e4695e', [halfWidth, 0, 0])}
    {grip(resizeVRef, '#8fd07a', [0, halfHeight, 0])}
    {grip(resizeUVRef, '#d6dae2', [halfWidth, halfHeight, 0])}
  </group>;
}

/** Reads the complex pressure under the pointer so the viewport can label the
 * value the colormap is showing. Gizmo drags stop propagation on the capture
 * phase, so this bubble-phase listener stays quiet while a handle is active. */
function FieldPlaneProbe({ plane, field, unitsPerMetre }: Pick<ReadyFieldPlaneProps, 'plane' | 'field' | 'unitsPerMetre'>) {
  const gl = useThree((state) => state.gl);
  const camera = useThree((state) => state.camera);

  useEffect(() => {
    const element = gl.domElement;
    const raycaster = new Raycaster();
    const hide = () => useFieldPlaneProbeStore.getState().hide();
    // A held button means the pointer is orbiting or dragging a handle, not
    // asking what the colour under it means.
    let pressed = false;
    const onPointerDown = () => {
      pressed = true;
      hide();
    };
    const onPointerUp = () => { pressed = false; };
    const onPointerMove = (event: PointerEvent) => {
      if (pressed || useFieldPlaneStore.getState().dragging) {
        hide();
        return;
      }
      const bounds = element.getBoundingClientRect();
      const hit = probeFieldPlaneRay(plane, rayFromPointer(event, element, camera, raycaster), unitsPerMetre);
      const sample = hit ? sampleFieldPlaneBilinear(field, hit.u, hit.v) : null;
      if (!hit || !sample) {
        hide();
        return;
      }
      const maskState = useFieldPlaneMaskStore.getState();
      const mask = maskMatchesGeometry(maskState, field.header.job_id, field.header.geometry_sha256)
        ? maskState.mask
        : null;
      useFieldPlaneProbeStore.getState().show({
        localX: event.clientX - bounds.left,
        localY: event.clientY - bounds.top,
        hostWidth: bounds.width,
        hostHeight: bounds.height,
        offsetU_m: hit.offsetU_m,
        offsetV_m: hit.offsetV_m,
        point_m: hit.point_m,
        real: sample.real,
        imag: sample.imag,
        masked: fieldPlaneMaskedAt(mask, hit.u, hit.v),
      });
    };
    element.addEventListener('pointermove', onPointerMove);
    element.addEventListener('pointerleave', hide);
    element.addEventListener('pointerdown', onPointerDown);
    element.addEventListener('pointerup', onPointerUp);
    element.addEventListener('pointercancel', onPointerUp);
    return () => {
      element.removeEventListener('pointermove', onPointerMove);
      element.removeEventListener('pointerleave', hide);
      element.removeEventListener('pointerdown', onPointerDown);
      element.removeEventListener('pointerup', onPointerUp);
      element.removeEventListener('pointercancel', onPointerUp);
      hide();
    };
  }, [camera, field, gl, plane, unitsPerMetre]);

  return null;
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
      {field && <FieldPlaneProbe plane={plane} field={field} unitsPerMetre={props.unitsPerMetre}/>}
      <FieldPlaneHandles plane={plane} unitsPerMetre={props.unitsPerMetre} scheduler={props.scheduler}/>
    </>}
  </>;
}
