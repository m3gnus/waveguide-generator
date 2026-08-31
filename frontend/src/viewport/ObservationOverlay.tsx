/**
 * The measurement rig, drawn where the solve measured it.
 *
 * One arc per enabled plane at the measurement distance, a marker at every
 * angle the sweep samples, and the pivot the whole thing turns about. The
 * markers are the same points `buildPolarFrdSet` writes a file for and the
 * SPL/phase cards read a response from, so pressing one here selects the curve
 * there -- which is the shortest description of what this is for.
 *
 * Dragging a marker moves it along its arc and selects the angle it lands on.
 * Holding shift drags radially instead and changes the measurement distance,
 * which is a solve setting: the rig moves at once, and the run on screen keeps
 * saying what it was measured at until it is solved again.
 */
import { useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  InstancedMesh,
  Line,
  LineBasicMaterial,
  Matrix4,
  MeshBasicMaterial,
  Raycaster,
  SphereGeometry,
  Vector2,
  Vector3,
} from 'three';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { MAX_MEASUREMENT_ANGLES } from '../results/measurementAngle';
import type { DemandRenderScheduler } from './demandRender';
import {
  observationRig,
  transverseFor,
  type ObservationPlane,
  type ObservationRigSpec,
} from './observationRig';
import { useObservationStore } from './observationStore';

/** Marker size as a fraction of the measurement radius. */
const MARKER_SCALE = 0.014;
/** The pivot marker, drawn smaller than a microphone so it reads as a hinge. */
const PIVOT_SCALE = 0.009;
const MIN_DISTANCE_M = 0.1;

const PLANE_COLORS: Record<ObservationPlane, string> = {
  horizontal: '#5fb0ff',
  vertical: '#ffb454',
  diagonal: '#8ee08a',
};

interface Drag {
  pointerId: number;
  index: number;
  /** Shift held at press: a radial drag changing distance, not an angular one. */
  radial: boolean;
}

function planeIntersection(
  rayOrigin: Vector3,
  rayDirection: Vector3,
  planeOrigin: Vector3,
  normal: Vector3,
): Vector3 | null {
  const denominator = rayDirection.dot(normal);
  if (Math.abs(denominator) < 1e-9) return null;
  const t = planeOrigin.clone().sub(rayOrigin).dot(normal) / denominator;
  if (!Number.isFinite(t)) return null;
  return rayOrigin.clone().addScaledVector(rayDirection, t);
}

/** The rig the solve options currently describe, on the frame the run published. */
export function rigSpecFromSolveOptions(polar: {
  distance: number;
  angleStart: number;
  angleEnd: number;
  angleStep: number;
  enabledAxes: readonly string[];
  diagonalAngle: number;
  observationOrigin: string;
}): ObservationRigSpec {
  const span = polar.angleEnd - polar.angleStart;
  const step = Math.abs(polar.angleStep) > 1e-9 ? Math.abs(polar.angleStep) : span;
  const count = span > 0 && step > 0 ? Math.max(2, Math.round(span / step) + 1) : 1;
  return {
    distanceM: polar.distance,
    angleStartDeg: polar.angleStart,
    angleEndDeg: polar.angleEnd,
    angleCount: count,
    planes: polar.enabledAxes.filter((axis): axis is ObservationPlane => (
      axis === 'horizontal' || axis === 'vertical' || axis === 'diagonal'
    )),
    inclinationDeg: polar.diagonalAngle,
    origin: polar.observationOrigin === 'throat' ? 'throat' : 'mouth',
  };
}

export function ObservationOverlay({ unitsPerMetre, scheduler }: {
  unitsPerMetre: number;
  scheduler: DemandRenderScheduler;
}) {
  const visible = useObservationStore((state) => state.visible);
  const basis = useObservationStore((state) => state.basis);
  const polar = useSolveOptionsStore((state) => state.polar);
  const preferences = usePreferences();
  const gl = useThree((state) => state.gl);
  const camera = useThree((state) => state.camera);
  const markers = useRef<InstancedMesh>(null);
  const drag = useRef<Drag | null>(null);

  const spec = useMemo(() => rigSpecFromSolveOptions(polar), [polar]);
  const rig = useMemo(
    () => (basis ? observationRig(basis, spec, unitsPerMetre) : null),
    [basis, spec, unitsPerMetre],
  );
  const radius = spec.distanceM * unitsPerMetre;
  const markerRadius = Math.max(radius * MARKER_SCALE, 1e-6);

  // Built as three.js objects and mounted through `primitive`: the `line`
  // intrinsic resolves to the SVG element under this TypeScript config, and an
  // arc is one draw call either way.
  const arcs = useMemo(() => (rig?.arcs ?? []).map(({ plane, positions }) => {
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(positions, 3));
    const material = new LineBasicMaterial({
      color: PLANE_COLORS[plane],
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
    });
    const object = new Line(geometry, material);
    object.renderOrder = 1_200;
    return { plane, object };
  }), [rig]);
  useEffect(() => () => arcs.forEach(({ object }) => {
    object.geometry.dispose();
    (object.material as LineBasicMaterial).dispose();
  }), [arcs]);

  const markerMaterial = useMemo(() => new MeshBasicMaterial({ toneMapped: false }), []);
  const markerGeometry = useMemo(() => new SphereGeometry(1, 12, 8), []);
  const pivotMaterial = useMemo(() => new MeshBasicMaterial({ color: '#e8e2d8', toneMapped: false }), []);
  useEffect(() => () => {
    markerMaterial.dispose();
    markerGeometry.dispose();
    pivotMaterial.dispose();
  }, [markerGeometry, markerMaterial, pivotMaterial]);

  /**
   * Which markers are drawn as selected: the angles the SPL and phase cards
   * are reading, in the plane they are reading them from. A marker in another
   * plane is dimmed rather than hidden -- the rig is also there to show where
   * the run measured, not only what is on a chart.
   */
  const selected = useMemo(() => {
    const chosen = new Set(preferences.measurementAngles);
    return (rig?.microphones ?? []).map(({ plane, angleDeg }) => (
      plane === preferences.measurementPlane && (chosen.has(angleDeg) || angleDeg === 0)
    ));
  }, [preferences.measurementAngles, preferences.measurementPlane, rig]);

  // Instance transforms and colours. Rebuilt whenever the rig or the selection
  // moves, then handed to the renderer as a single draw call.
  useEffect(() => {
    const mesh = markers.current;
    if (!mesh || !rig) return;
    const matrix = new Matrix4();
    const color = new Color();
    rig.microphones.forEach((microphone, index) => {
      const scale = selected[index] ? markerRadius * 1.6 : markerRadius;
      matrix.makeScale(scale, scale, scale);
      matrix.setPosition(...microphone.position);
      mesh.setMatrixAt(index, matrix);
      color.set(PLANE_COLORS[microphone.plane]);
      if (!selected[index]) color.multiplyScalar(0.42);
      mesh.setColorAt(index, color);
    });
    mesh.count = rig.microphones.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
    scheduler.schedule();
  }, [markerRadius, rig, scheduler, selected]);

  useEffect(() => {
    scheduler.schedule();
  }, [rig, scheduler, visible]);

  useEffect(() => {
    if (!visible || !rig || !basis) return undefined;
    const element = gl.domElement;
    const raycaster = new Raycaster();
    const pointerRay = (event: PointerEvent) => {
      const bounds = element.getBoundingClientRect();
      raycaster.setFromCamera(new Vector2(
        ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
        -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
      ), camera);
      return raycaster.ray;
    };
    const originVector = new Vector3(...rig.origin);
    const axisVector = new Vector3(...basis.axis);

    /** Where the pointer lands on the plane of the microphone being dragged. */
    const pointOnPlane = (event: PointerEvent, plane: ObservationPlane): Vector3 | null => {
      const ray = pointerRay(event);
      const transverse = new Vector3(...transverseFor(basis, plane, spec.inclinationDeg));
      const normal = axisVector.clone().cross(transverse).normalize();
      return planeIntersection(ray.origin, ray.direction, originVector, normal);
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || drag.current || !markers.current) return;
      pointerRay(event);
      const hit = raycaster.intersectObject(markers.current, false)[0];
      if (hit?.instanceId === undefined) return;
      // Claim the press before OrbitControls sees it, exactly as the field
      // plane handles do.
      event.preventDefault();
      event.stopPropagation();
      element.setPointerCapture(event.pointerId);
      drag.current = { pointerId: event.pointerId, index: hit.instanceId, radial: event.shiftKey };
      const microphone = rig.microphones[hit.instanceId];
      if (microphone) useObservationStore.getState().setHovered(microphone);
    };

    /** Snap an angle to the nearest one this sweep actually samples. */
    const snap = (angleDeg: number): number => {
      const angles = rig.microphones.map((microphone) => microphone.angleDeg);
      return angles.reduce((best, candidate) => (
        Math.abs(candidate - angleDeg) < Math.abs(best - angleDeg) ? candidate : best
      ), angles[0] ?? 0);
    };

    const onPointerMove = (event: PointerEvent) => {
      const active = drag.current;
      if (!active || active.pointerId !== event.pointerId) return;
      const microphone = rig.microphones[active.index];
      if (!microphone) return;
      event.preventDefault();
      event.stopPropagation();
      const point = pointOnPlane(event, microphone.plane);
      if (!point) return;
      if (active.radial) {
        // Radial: the whole rig's distance, which is a solve setting. Rounded
        // to the centimetre so a drag produces a number a person would type.
        const distance = point.distanceTo(originVector) / unitsPerMetre;
        const next = Math.max(MIN_DISTANCE_M, Math.round(distance * 100) / 100);
        if (next !== useSolveOptionsStore.getState().polar.distance) {
          useSolveOptionsStore.getState().updatePolar({ distance: next });
        }
        return;
      }
      const relative = point.clone().sub(originVector);
      const transverse = new Vector3(...transverseFor(basis, microphone.plane, spec.inclinationDeg));
      const angle = (Math.atan2(relative.dot(transverse), relative.dot(axisVector)) * 180) / Math.PI;
      const snapped = snap(angle);
      useObservationStore.getState().setHovered({ plane: microphone.plane, angleDeg: snapped });
      const state = preferencesStore.getSnapshot();
      const others = state.measurementAngles.filter((candidate) => candidate !== snapped);
      preferencesStore.update({
        measurementPlane: microphone.plane,
        measurementAngles: [snapped, ...others].slice(0, MAX_MEASUREMENT_ANGLES - 1).sort((left, right) => left - right),
      });
    };

    const endDrag = (event: PointerEvent) => {
      const active = drag.current;
      if (!active || active.pointerId !== event.pointerId) return;
      drag.current = null;
      if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
    };

    element.addEventListener('pointerdown', onPointerDown, { capture: true });
    element.addEventListener('pointermove', onPointerMove, { capture: true });
    element.addEventListener('pointerup', endDrag, { capture: true });
    element.addEventListener('pointercancel', endDrag, { capture: true });
    return () => {
      element.removeEventListener('pointerdown', onPointerDown, { capture: true });
      element.removeEventListener('pointermove', onPointerMove, { capture: true });
      element.removeEventListener('pointerup', endDrag, { capture: true });
      element.removeEventListener('pointercancel', endDrag, { capture: true });
      drag.current = null;
    };
  }, [basis, camera, gl, rig, spec, unitsPerMetre, visible]);

  if (!visible || !rig) return null;
  const pivotRadius = Math.max(radius * PIVOT_SCALE, 1e-6);
  return <group renderOrder={1_200}>
    {arcs.map(({ plane, object }) => <primitive key={plane} object={object}/>)}
    <instancedMesh
      ref={markers}
      args={[markerGeometry, markerMaterial, Math.max(1, rig.microphones.length)]}
      frustumCulled={false}
    />
    <mesh position={rig.origin} material={pivotMaterial}>
      <sphereGeometry args={[pivotRadius, 10, 8]}/>
    </mesh>
  </group>;
}
