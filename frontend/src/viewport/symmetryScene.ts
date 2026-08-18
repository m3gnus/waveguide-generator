import { Box3, Vector3 } from 'three';
import type { FrameScene } from './frameScene';
import type { SceneSurface } from './types';

export type DisplayQuadrants = 1 | 12 | 14 | 1234;
type MirrorAxis = 0 | 1;

const PLANE_EPSILON = 1e-8;

function sceneBounds(surfaces: readonly SceneSurface[]): Box3 {
  const bounds = new Box3();
  const point = new Vector3();
  for (const surface of surfaces) {
    for (let offset = 0; offset < surface.positions.length; offset += 3) {
      bounds.expandByPoint(point.set(
        surface.positions[offset],
        surface.positions[offset + 1],
        surface.positions[offset + 2],
      ));
    }
  }
  if (bounds.isEmpty()) bounds.set(new Vector3(-1, -1, -1), new Vector3(1, 1, 1));
  return bounds;
}

function liesOnPlane(surface: SceneSurface, axis: MirrorAxis): boolean {
  for (let offset = axis; offset < surface.positions.length; offset += 3) {
    if (Math.abs(surface.positions[offset]) > PLANE_EPSILON) return false;
  }
  return true;
}

function mirroredSurface(surface: SceneSurface, axis: MirrorAxis, suffix: string): SceneSurface {
  const positions = surface.positions.slice();
  const normals = surface.normals.slice();
  for (let offset = axis; offset < positions.length; offset += 3) positions[offset] *= -1;
  for (let offset = axis; offset < normals.length; offset += 3) normals[offset] *= -1;
  const indices = surface.indices.slice();
  // A reflection has negative determinant. Reverse every face so front-face
  // culling and the reflected vertex normals still describe the same exterior.
  for (let offset = 0; offset + 2 < indices.length; offset += 3) {
    const second = indices[offset + 1];
    indices[offset + 1] = indices[offset + 2];
    indices[offset + 2] = second;
  }
  return {
    ...surface,
    key: `${surface.key}:mirror-${suffix}`,
    positions,
    normals,
    indices,
    solvedDomain: false,
  };
}

/** Expand a positive-side solver domain back to the complete physical model.
 * The original triangles remain marked as the domain actually assembled and
 * solved; reflected display-only copies are deliberately unmarked. */
export function expandImportedSymmetry(
  scene: FrameScene,
  cutPlanes: readonly string[],
): FrameScene {
  const axes: MirrorAxis[] = [
    ...(cutPlanes.includes('x0') ? [0 as const] : []),
    ...(cutPlanes.includes('y0') ? [1 as const] : []),
  ];
  if (!axes.length) return {
    ...scene,
    surfaces: scene.surfaces.map((surface) => ({ ...surface, solvedDomain: false })),
  };
  let surfaces: SceneSurface[] = scene.surfaces.map((surface) => ({ ...surface, solvedDomain: true }));
  axes.forEach((axis) => {
    const existing = surfaces;
    const suffix = axis === 0 ? 'x' : 'y';
    surfaces = [
      ...existing,
      ...existing
        .filter((surface) => !liesOnPlane(surface, axis))
        .map((surface) => mirroredSurface(surface, axis, suffix)),
    ];
  });
  return { ...scene, surfaces, bounds: sceneBounds(surfaces) };
}

function triangleInSolvedDomain(
  surface: SceneSurface,
  triangleOffset: number,
  quadrants: Exclude<DisplayQuadrants, 1234>,
): boolean {
  const a = surface.indices[triangleOffset] * 3;
  const b = surface.indices[triangleOffset + 1] * 3;
  const c = surface.indices[triangleOffset + 2] * 3;
  const x = (surface.positions[a] + surface.positions[b] + surface.positions[c]) / 3;
  // Preview geometry is always built in the recentred frame the solver runs
  // in — Mesh.VerticalOffset is a CAD-boundary placement that never reaches
  // it — so the centroid can be classified against the origin cut planes
  // directly.
  const y = (
    surface.positions[a + 1]
    + surface.positions[b + 1]
    + surface.positions[c + 1]
  ) / 3;
  if (quadrants === 12) return y >= -PLANE_EPSILON;
  if (quadrants === 14) return x >= -PLANE_EPSILON;
  return x >= -PLANE_EPSILON && y >= -PLANE_EPSILON;
}

/** Split a full parametric preview by triangle centroid so the region selected
 * for a reduced-domain solve can receive a tint without hiding the rest. */
export function markParametricSolvedDomain(
  scene: FrameScene,
  quadrants: DisplayQuadrants,
): FrameScene {
  if (quadrants === 1234) return {
    ...scene,
    surfaces: scene.surfaces.map((surface) => ({ ...surface, solvedDomain: false })),
  };
  const surfaces = scene.surfaces.flatMap((surface): SceneSurface[] => {
    const solved: number[] = [];
    const displayOnly: number[] = [];
    for (let offset = 0; offset + 2 < surface.indices.length; offset += 3) {
      const target = triangleInSolvedDomain(surface, offset, quadrants) ? solved : displayOnly;
      target.push(surface.indices[offset], surface.indices[offset + 1], surface.indices[offset + 2]);
    }
    return [
      ...(solved.length ? [{ ...surface, key: `${surface.key}:solved-${quadrants}`, indices: Uint32Array.from(solved), solvedDomain: true }] : []),
      ...(displayOnly.length ? [{ ...surface, key: `${surface.key}:display-${quadrants}`, indices: Uint32Array.from(displayOnly), solvedDomain: false }] : []),
    ];
  });
  return { ...scene, surfaces };
}

/** Map origin cut planes to the positive-side domain retained by the solver.
 * This is shared by parametric symmetry tinting and full-domain CAD viewport
 * artifacts, whose triangles must be classified without being mirrored. */
export function quadrantsForCutPlanes(cutPlanes: readonly string[]): DisplayQuadrants {
  const cutX = cutPlanes.includes('x0');
  const cutY = cutPlanes.includes('y0');
  if (cutX && cutY) return 1;
  if (cutX) return 14;
  if (cutY) return 12;
  return 1234;
}

export function quadrantsForSolveMode(
  mode: 'auto' | 'full' | 'half_xz' | 'half_yz' | 'quarter',
  autoQuadrants: DisplayQuadrants = 1234,
): DisplayQuadrants {
  if (mode === 'quarter') return 1;
  if (mode === 'half_xz') return 12;
  if (mode === 'half_yz') return 14;
  return mode === 'auto' ? autoQuadrants : 1234;
}
