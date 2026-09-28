import { useEffect, useMemo } from 'react';
import { BufferGeometry, DoubleSide, Float32BufferAttribute, type Box3 } from 'three';
import type { DemandRenderScheduler } from './demandRender';
import type { ViewportTheme } from './types';

export type MarkedPlane = 'x0' | 'y0';

/** The planes to mark on a scene: a reduced CAD solve's mirror planes, when
 * the viewer asks for them ("Mark symmetry planes"); none otherwise. */
export function planesToMark(
  scene: { symmetryPlanes?: ReadonlyArray<MarkedPlane> } | null,
  preferences: { markSymmetryPlanes: boolean },
): ReadonlyArray<MarkedPlane> {
  return preferences.markSymmetryPlanes ? scene?.symmetryPlanes ?? [] : [];
}

/** How far the mark reaches past the model on each side, as a share of its span. */
const MARGIN = 0.04;

/**
 * The rectangle that marks one origin mirror plane across a model's bounds:
 * four corners, in order, lying in the plane (x = 0 or y = 0) and spanning the
 * other two axes a little past the model. Pure, so it can be tested without
 * a renderer.
 */
export function symmetryPlaneOutline(bounds: Box3, plane: MarkedPlane): number[] {
  const min = bounds.min;
  const max = bounds.max;
  const pad = (low: number, high: number): [number, number] => {
    const span = Math.max(high - low, 1e-9);
    return [low - span * MARGIN, high + span * MARGIN];
  };
  const [z0, z1] = pad(min.z, max.z);
  if (plane === 'x0') {
    const [y0, y1] = pad(min.y, max.y);
    return [0, y0, z0, 0, y1, z0, 0, y1, z1, 0, y0, z1];
  }
  const [x0, x1] = pad(min.x, max.x);
  return [x0, 0, z0, x1, 0, z0, x1, 0, z1, x0, 0, z1];
}

function planeGeometries(bounds: Box3, plane: MarkedPlane): { outline: BufferGeometry; fill: BufferGeometry } {
  const corners = symmetryPlaneOutline(bounds, plane);
  const outline = new BufferGeometry();
  outline.setAttribute('position', new Float32BufferAttribute(corners, 3));
  const fill = new BufferGeometry();
  fill.setAttribute('position', new Float32BufferAttribute(corners, 3));
  fill.setIndex([0, 1, 2, 0, 2, 3]);
  return { outline, fill };
}

/**
 * A faint outline, and fainter fill, on each mirror plane of a reduced CAD
 * solve: where the whole model on screen was reflected from the piece the
 * solver assembles. Display only, never picked, never written to depth, so
 * it neither hides the model nor changes what a click selects.
 */
export function SymmetryPlaneMarks({ bounds, planes, theme, scheduler }: {
  bounds: Box3;
  planes: ReadonlyArray<MarkedPlane>;
  theme: ViewportTheme;
  scheduler: DemandRenderScheduler;
}) {
  const geometries = useMemo(
    () => planes.map((plane) => ({ plane, ...planeGeometries(bounds, plane) })),
    [bounds, planes],
  );
  useEffect(() => {
    scheduler.schedule();
    return () => {
      geometries.forEach(({ outline, fill }) => { outline.dispose(); fill.dispose(); });
      scheduler.schedule();
    };
  }, [geometries, scheduler]);
  const color = theme === 'light' ? '#3d5a78' : '#9fb9d4';
  return <group name="symmetry-planes">
    {geometries.map(({ plane, outline, fill }) => <group key={plane} name={`symmetry-plane-${plane}`}>
      <lineLoop geometry={outline} renderOrder={2} raycast={() => null}>
        <lineBasicMaterial color={color} transparent opacity={0.45} depthWrite={false} />
      </lineLoop>
      <mesh geometry={fill} renderOrder={1} raycast={() => null}>
        <meshBasicMaterial color={color} transparent opacity={0.05} side={DoubleSide} depthWrite={false} />
      </mesh>
    </group>)}
  </group>;
}
