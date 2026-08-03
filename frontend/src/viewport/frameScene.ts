import { Box3, Vector3 } from 'three';
import type { DecodedFrame, FrameArray, FrameSurface } from '../api/frame';
import type { SceneSurface, SurfaceMaterialClass } from './types';

export interface FrameScene {
  surfaces: SceneSurface[];
  bounds: Box3;
  hasCurvature: boolean;
}

function section<T extends FrameArray>(frame: DecodedFrame, name: string, constructor: { new (...args: never[]): T }): T {
  const value = frame.sections[name];
  if (!(value instanceof constructor)) throw new Error(`Surface section ${name} has the wrong type`);
  return value;
}

export function isEnclosureRole(role: string): boolean {
  return role === 'enclosure' || role.startsWith('enclosure.');
}

export function materialClassForSurface(surface: Pick<FrameSurface, 'role' | 'shading'>): SurfaceMaterialClass {
  const family = isEnclosureRole(surface.role)
    ? 'enclosure'
    : surface.role === 'horn.inner' || surface.role === 'horn.outer'
      ? 'horn'
      : 'boundary';
  return `${family}-${surface.shading}` as SurfaceMaterialClass;
}

function curvatureName(frame: DecodedFrame, surface: FrameSurface): string | null {
  const declared = surface.curvatureMean ?? surface.curvaturePrincipal;
  if (declared && frame.sections[declared] instanceof Float32Array) return declared;
  const prefix = surface.positions.endsWith('.positions')
    ? surface.positions.slice(0, -'.positions'.length)
    : surface.positions;
  for (const candidate of [`${prefix}.curvatureMean`, `${prefix}.curvaturePrincipal`]) {
    if (frame.sections[candidate] instanceof Float32Array) return candidate;
  }
  if ((frame.header.surfaces?.length ?? 0) === 1) {
    if (frame.sections.curvatureMean instanceof Float32Array) return 'curvatureMean';
    if (frame.sections.curvaturePrincipal instanceof Float32Array) return 'curvaturePrincipal';
  }
  return null;
}

export function frameToScene(frame: DecodedFrame): FrameScene {
  const bounds = new Box3();
  const point = new Vector3();
  const surfaces = (frame.header.surfaces ?? []).map((surface, index): SceneSurface => {
    const positions = section(frame, surface.positions, Float32Array);
    const normals = section(frame, surface.normals, Float32Array);
    const indices = section(frame, surface.indices, Uint32Array);
    const curvatureSection = curvatureName(frame, surface);
    const curvatureValues = curvatureSection ? section(frame, curvatureSection, Float32Array) : null;
    const curvature = curvatureValues?.length === positions.length / 3 ? curvatureValues : null;
    for (let offset = 0; offset < positions.length; offset += 3) {
      point.set(positions[offset], positions[offset + 1], positions[offset + 2]);
      bounds.expandByPoint(point);
    }
    return {
      key: `${index}:${surface.role}:${surface.positions}`,
      role: surface.role,
      shading: surface.shading,
      materialClass: materialClassForSurface(surface),
      enclosure: isEnclosureRole(surface.role),
      positions,
      normals,
      indices,
      curvature,
    };
  });
  if (bounds.isEmpty()) bounds.set(new Vector3(-50, -50, -50), new Vector3(50, 50, 50));
  return { surfaces, bounds, hasCurvature: surfaces.some((surface) => surface.curvature !== null) };
}

export function curvatureColors(values: Float32Array): Float32Array {
  let limit = 0;
  for (const value of values) limit = Math.max(limit, Math.abs(value));
  if (limit === 0) limit = 1;
  const colors = new Float32Array(values.length * 3);
  for (let index = 0; index < values.length; index += 1) {
    const normalized = Math.max(-1, Math.min(1, values[index] / limit));
    const warm = Math.max(0, normalized);
    const cool = Math.max(0, -normalized);
    const center = 1 - Math.abs(normalized);
    colors[index * 3] = 0.18 + warm * 0.82 + center * 0.42;
    colors[index * 3 + 1] = 0.2 + center * 0.62;
    colors[index * 3 + 2] = 0.2 + cool * 0.8 + center * 0.48;
  }
  return colors;
}

export function surfaceBoundaryPositions(surface: SceneSurface): Float32Array {
  const edgeCounts = new Map<string, { count: number; a: number; b: number }>();
  const add = (a: number, b: number) => {
    const low = Math.min(a, b);
    const high = Math.max(a, b);
    const key = `${low}:${high}`;
    const previous = edgeCounts.get(key);
    if (previous) previous.count += 1;
    else edgeCounts.set(key, { count: 1, a, b });
  };
  for (let offset = 0; offset + 2 < surface.indices.length; offset += 3) {
    const a = surface.indices[offset];
    const b = surface.indices[offset + 1];
    const c = surface.indices[offset + 2];
    add(a, b);
    add(b, c);
    add(c, a);
  }
  const boundary = [...edgeCounts.values()].filter((edge) => edge.count === 1);
  const positions = new Float32Array(boundary.length * 6);
  boundary.forEach((edge, index) => {
    const target = index * 6;
    const a = edge.a * 3;
    const b = edge.b * 3;
    positions[target] = surface.positions[a];
    positions[target + 1] = surface.positions[a + 1];
    positions[target + 2] = surface.positions[a + 2];
    positions[target + 3] = surface.positions[b];
    positions[target + 4] = surface.positions[b + 1];
    positions[target + 5] = surface.positions[b + 2];
  });
  return positions;
}
