/**
 * FEM interior field display for declared FEM regions (fem-regions-v1).
 *
 * Modelled on Boundary Lab's interior observation planes. The tetrahedral air
 * is not coloured on its skin; instead a plane cuts through it and the cut is
 * coloured by the solved nodal pressure:
 *
 * - **Smooth field.** Each tetrahedron the plane crosses contributes the exact
 *   polygon of the cut. The pressure at a cut vertex is interpolated along the
 *   tetrahedron edge it lies on, which is exact for P1 (linear) elements, so
 *   the slice needs no point location or sampling grid and has no gaps.
 * - **Element field.** The same polygons coloured flat with the tetrahedron's
 *   mean pressure, so the element structure stays visible.
 * - **Clipped context.** The tetrahedra on the kept side of the plane are drawn
 *   as a faint ghost with their edges, the way Boundary Lab shows the
 *   clipped half-volume, and the region's boundary surfaces are hidden while a
 *   slice is shown so the cut is not covered by walls.
 *
 * Boundary surfaces carry Boundary Lab's role palette: rigid walls grey,
 * driven diaphragms blue, interfaces green.
 *
 * Values are complex nodal pressure in WG's phasor convention, so the
 * existing field-plane colour functions (SPL, normalized, phase,
 * instantaneous) apply unchanged.
 */

import {
  buildLutRgba,
  fieldPlaneColormap,
  fieldPlaneDisplayValue,
  fieldPlaneWindowForMode,
  sampleLut,
  windowNormalize,
  type FieldPlaneDisplayMode,
  type FieldPlaneValueWindow,
} from './fieldPlaneColor';

export type FemSurfaceKind = 'rigid' | 'moving' | 'interface' | 'plane-wave-termination';

export interface FemSurface {
  /** WG's physical name: wg-fem-v1|region=..|group=..|kind=.. */
  name: string;
  kind: FemSurfaceKind;
  /** Zero-based vertex indices, three per triangle. */
  triangles: Uint32Array;
}

export interface FemFieldDomain {
  regionId: string;
  /** Vertex coordinates in millimetres, xyz interleaved. */
  points: Float32Array;
  /** Zero-based vertex indices, four per tetrahedron. */
  tetrahedra: Uint32Array;
  surfaces: FemSurface[];
}

export interface ComplexNodal {
  real: Float32Array;
  imag: Float32Array;
}

export interface SlicePlane {
  origin: readonly [number, number, number];
  /** Need not be unit length; the kept ghost side is where normal·(x-origin) >= 0. */
  normal: readonly [number, number, number];
}

export interface FemSlice {
  /** Triangle soup, xyz per vertex, three vertices per triangle. */
  positions: Float32Array;
  /** Interpolated complex pressure per slice vertex. */
  real: Float32Array;
  imag: Float32Array;
  /** The tetrahedron each slice triangle came from. */
  tetrahedron: Uint32Array;
}

export const FEM_ROLE_COLORS: Readonly<Record<FemSurfaceKind, { fill: string; edge: string }>> = {
  rigid: { fill: '#cfcfcf', edge: '#555555' },
  moving: { fill: '#3292bf', edge: '#20343c' },
  interface: { fill: '#1cad0c', edge: '#155b0d' },
  'plane-wave-termination': { fill: '#d9a33b', edge: '#6b4d12' },
};

export const FEM_GHOST_STYLE = {
  fill: '#91a4ad',
  edge: '#63747c',
  opacity: 0.12,
} as const;

const PHYSICAL_NAMESPACE = 'wg-fem-v1';

/** Read the kind out of a WG FEM physical name; rigid for anything unknown. */
export function femSurfaceKind(name: string): FemSurfaceKind {
  const parts = name.split('|');
  if (parts[0] !== PHYSICAL_NAMESPACE) return 'rigid';
  for (const part of parts.slice(1)) {
    const [key, value] = part.split('=');
    if (key === 'kind' && value in FEM_ROLE_COLORS) return value as FemSurfaceKind;
  }
  return 'rigid';
}

function signedDistances(points: Float32Array, plane: SlicePlane): Float64Array {
  const [nx, ny, nz] = plane.normal;
  const length = Math.hypot(nx, ny, nz);
  if (!(length > 0)) throw new Error('A slice plane needs a non-zero normal');
  const [ox, oy, oz] = plane.origin;
  const count = points.length / 3;
  const distances = new Float64Array(count);
  for (let vertex = 0; vertex < count; vertex += 1) {
    distances[vertex] = (
      (points[vertex * 3] - ox) * nx
      + (points[vertex * 3 + 1] - oy) * ny
      + (points[vertex * 3 + 2] - oz) * nz
    ) / length;
  }
  return distances;
}

const TET_EDGES: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3],
];

/**
 * Cut every tetrahedron the plane crosses.
 *
 * A tetrahedron with vertices on both sides yields a triangle (one vertex
 * alone on a side) or a quadrilateral (two and two), split into two
 * triangles. Vertices lying exactly on the plane count as the positive side,
 * so a face lying in the plane is drawn once, by the tetrahedron below it.
 */
export function sliceFemField(domain: FemFieldDomain, values: ComplexNodal, plane: SlicePlane): FemSlice {
  const { points, tetrahedra } = domain;
  const vertexCount = points.length / 3;
  if (values.real.length !== vertexCount || values.imag.length !== vertexCount) {
    throw new Error('Nodal values must have one complex value per FEM vertex');
  }
  const distance = signedDistances(points, plane);
  const positions: number[] = [];
  const real: number[] = [];
  const imag: number[] = [];
  const source: number[] = [];
  const cut: number[][] = [];
  const tetCount = tetrahedra.length / 4;
  for (let tet = 0; tet < tetCount; tet += 1) {
    const corner = [
      tetrahedra[tet * 4], tetrahedra[tet * 4 + 1], tetrahedra[tet * 4 + 2], tetrahedra[tet * 4 + 3],
    ];
    let below = 0;
    for (const vertex of corner) if (distance[vertex] < 0) below += 1;
    if (below === 0 || below === 4) continue;
    cut.length = 0;
    for (const [a, b] of TET_EDGES) {
      const va = corner[a];
      const vb = corner[b];
      const da = distance[va];
      const db = distance[vb];
      if ((da < 0) === (db < 0)) continue;
      const t = da / (da - db);
      cut.push([
        points[va * 3] + t * (points[vb * 3] - points[va * 3]),
        points[va * 3 + 1] + t * (points[vb * 3 + 1] - points[va * 3 + 1]),
        points[va * 3 + 2] + t * (points[vb * 3 + 2] - points[va * 3 + 2]),
        values.real[va] + t * (values.real[vb] - values.real[va]),
        values.imag[va] + t * (values.imag[vb] - values.imag[va]),
      ]);
    }
    const polygon = orderAroundCentroid(cut, plane.normal);
    for (let fan = 1; fan + 1 < polygon.length; fan += 1) {
      for (const vertex of [polygon[0], polygon[fan], polygon[fan + 1]]) {
        positions.push(vertex[0], vertex[1], vertex[2]);
        real.push(vertex[3]);
        imag.push(vertex[4]);
      }
      source.push(tet);
    }
  }
  return {
    positions: Float32Array.from(positions),
    real: Float32Array.from(real),
    imag: Float32Array.from(imag),
    tetrahedron: Uint32Array.from(source),
  };
}

/** Order a convex cut polygon counter-clockwise seen along the plane normal. */
function orderAroundCentroid(vertices: number[][], normal: readonly [number, number, number]): number[][] {
  if (vertices.length <= 3) {
    return vertices.length === 3 && windingAgainst(vertices, normal) ? [vertices[0], vertices[2], vertices[1]] : vertices.slice();
  }
  const centre = [0, 0, 0];
  for (const vertex of vertices) for (let axis = 0; axis < 3; axis += 1) centre[axis] += vertex[axis] / vertices.length;
  const [u, v] = planeBasis(normal);
  return vertices
    .map((vertex) => {
      const dx = vertex[0] - centre[0];
      const dy = vertex[1] - centre[1];
      const dz = vertex[2] - centre[2];
      return { vertex, angle: Math.atan2(dx * v[0] + dy * v[1] + dz * v[2], dx * u[0] + dy * u[1] + dz * u[2]) };
    })
    .sort((left, right) => left.angle - right.angle)
    .map((entry) => entry.vertex);
}

function windingAgainst(triangle: number[][], normal: readonly [number, number, number]): boolean {
  const [a, b, c] = triangle;
  const e1 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const e2 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const cross = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
  return cross[0] * normal[0] + cross[1] * normal[1] + cross[2] * normal[2] < 0;
}

function planeBasis(normal: readonly [number, number, number]): [number[], number[]] {
  const length = Math.hypot(normal[0], normal[1], normal[2]);
  const n = [normal[0] / length, normal[1] / length, normal[2] / length];
  const helper = Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  const u = [
    helper[1] * n[2] - helper[2] * n[1],
    helper[2] * n[0] - helper[0] * n[2],
    helper[0] * n[1] - helper[1] * n[0],
  ];
  const ul = Math.hypot(u[0], u[1], u[2]);
  const unit = [u[0] / ul, u[1] / ul, u[2] / ul];
  const v = [
    n[1] * unit[2] - n[2] * unit[1],
    n[2] * unit[0] - n[0] * unit[2],
    n[0] * unit[1] - n[1] * unit[0],
  ];
  return [unit, v];
}

/** Boundary Lab's Element Field: each slice triangle takes its tetrahedron's mean. */
export function elementMeanSlice(domain: FemFieldDomain, values: ComplexNodal, slice: FemSlice): ComplexNodal {
  const real = new Float32Array(slice.real.length);
  const imag = new Float32Array(slice.imag.length);
  for (let triangle = 0; triangle < slice.tetrahedron.length; triangle += 1) {
    const tet = slice.tetrahedron[triangle];
    let sumReal = 0;
    let sumImag = 0;
    for (let corner = 0; corner < 4; corner += 1) {
      const vertex = domain.tetrahedra[tet * 4 + corner];
      sumReal += values.real[vertex];
      sumImag += values.imag[vertex];
    }
    for (let corner = 0; corner < 3; corner += 1) {
      real[triangle * 3 + corner] = sumReal / 4;
      imag[triangle * 3 + corner] = sumImag / 4;
    }
  }
  return { real, imag };
}

/**
 * The ghosted half-volume: unique edges of every tetrahedron with a vertex on
 * the kept side, as line-segment endpoints.
 */
export function clippedGhostEdges(domain: FemFieldDomain, plane: SlicePlane): Float32Array {
  const distance = signedDistances(domain.points, plane);
  const seen = new Set<number>();
  const segments: number[] = [];
  const vertexCount = domain.points.length / 3;
  const tetCount = domain.tetrahedra.length / 4;
  for (let tet = 0; tet < tetCount; tet += 1) {
    let kept = false;
    for (let corner = 0; corner < 4; corner += 1) {
      if (distance[domain.tetrahedra[tet * 4 + corner]] >= 0) kept = true;
    }
    if (!kept) continue;
    for (const [a, b] of TET_EDGES) {
      const va = domain.tetrahedra[tet * 4 + a];
      const vb = domain.tetrahedra[tet * 4 + b];
      const key = Math.min(va, vb) * vertexCount + Math.max(va, vb);
      if (seen.has(key)) continue;
      seen.add(key);
      for (const vertex of [va, vb]) {
        segments.push(domain.points[vertex * 3], domain.points[vertex * 3 + 1], domain.points[vertex * 3 + 2]);
      }
    }
  }
  return Float32Array.from(segments);
}

/** Linear RGB in 0..1 per vertex for a field, through the field-plane LUT. */
export function femFieldColors(
  values: ComplexNodal,
  mode: FieldPlaneDisplayMode,
  options: { sequential: readonly string[]; phaseRadians?: number; window?: FieldPlaneValueWindow; fieldMaxSplDb?: number },
): { colors: Float32Array; window: FieldPlaneValueWindow } {
  const window = options.window ?? fieldPlaneWindowForMode(mode, values.real, values.imag);
  const lut = buildLutRgba(fieldPlaneColormap(mode, options.sequential));
  const fieldMaxSplDb = options.fieldMaxSplDb ?? (mode === 'spl' ? window.maximum : 0);
  const colors = new Float32Array(values.real.length * 3);
  for (let vertex = 0; vertex < values.real.length; vertex += 1) {
    const value = fieldPlaneDisplayValue(mode, values.real[vertex], values.imag[vertex], {
      fieldMaxSplDb,
      phaseRadians: options.phaseRadians ?? 0,
    });
    const { rgba } = sampleLut(lut, windowNormalize(value, window.minimum, window.maximum));
    colors[vertex * 3] = rgba[0] / 255;
    colors[vertex * 3 + 1] = rgba[1] / 255;
    colors[vertex * 3 + 2] = rgba[2] / 255;
  }
  return { colors, window };
}

/**
 * The FEM-field wire payload a result endpoint serves: little-endian arrays
 * as base64, so the viewer decodes without a parser. One frequency and one
 * response (system or channel) per request, the way Boundary Lab synthesises
 * its interior planes.
 */
export interface FemFieldPayload {
  schema: 'wg-fem-field-v1';
  region_id: string;
  frequency_hz: number;
  points_mm: string; // float32 xyz
  tetrahedra: string; // uint32 x4
  surfaces: Array<{ name: string; triangles: string }>; // uint32 x3
  pressure_real: string; // float32 per vertex
  pressure_imag: string;
}

function decode<T extends Float32Array | Uint32Array>(text: string, kind: 'f32' | 'u32'): T {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  if (bytes.length % 4 !== 0) throw new Error('FEM field array is not 4-byte aligned');
  const view = new DataView(bytes.buffer);
  const count = bytes.length / 4;
  const output = kind === 'f32' ? new Float32Array(count) : new Uint32Array(count);
  for (let index = 0; index < count; index += 1) {
    output[index] = kind === 'f32' ? view.getFloat32(index * 4, true) : view.getUint32(index * 4, true);
  }
  return output as T;
}

export function decodeFemFieldPayload(payload: FemFieldPayload): { domain: FemFieldDomain; values: ComplexNodal; frequencyHz: number } {
  if (payload.schema !== 'wg-fem-field-v1') throw new Error(`Unsupported FEM field schema ${String(payload.schema)}`);
  if (!Number.isFinite(payload.frequency_hz) || payload.frequency_hz <= 0) throw new Error('FEM field frequency must be positive and finite');
  const points = decode<Float32Array>(payload.points_mm, 'f32');
  const tetrahedra = decode<Uint32Array>(payload.tetrahedra, 'u32');
  const vertexCount = points.length / 3;
  if (!Number.isInteger(vertexCount) || tetrahedra.length % 4 !== 0) throw new Error('FEM field arrays have the wrong shape');
  for (const vertex of tetrahedra) if (vertex >= vertexCount) throw new Error('FEM tetrahedron references a missing vertex');
  const values = {
    real: decode<Float32Array>(payload.pressure_real, 'f32'),
    imag: decode<Float32Array>(payload.pressure_imag, 'f32'),
  };
  if (values.real.length !== vertexCount || values.imag.length !== vertexCount) {
    throw new Error('FEM field pressure must have one value per vertex');
  }
  if ([points, values.real, values.imag].some((array) => array.some((value) => !Number.isFinite(value)))) {
    throw new Error('FEM field values must be finite');
  }
  const surfaces = payload.surfaces.map((surface) => {
    const triangles = decode<Uint32Array>(surface.triangles, 'u32');
    if (triangles.length % 3 !== 0) throw new Error(`FEM surface ${surface.name} is not made of triangles`);
    for (const vertex of triangles) if (vertex >= vertexCount) throw new Error('FEM surface references a missing vertex');
    return { name: surface.name, kind: femSurfaceKind(surface.name), triangles };
  });
  return { domain: { regionId: payload.region_id, points, tetrahedra, surfaces }, values, frequencyHz: payload.frequency_hz };
}

/** Exact P1 velocity, projected onto the plane normal in exp(-i omega t). */
export function femNormalVelocitySlice(domain: FemFieldDomain, values: ComplexNodal,
  plane: SlicePlane, frequencyHz: number, density = 1.21): FemSlice {
  if (!(frequencyHz > 0) || !(density > 0)) throw new Error('Velocity needs a positive frequency and density');
  const cut = sliceFemField(domain, values, plane);
  const norm = Math.hypot(...plane.normal);
  const normal = plane.normal.map((v) => v / norm);
  const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a: number[], b: number[]) => a.reduce((s, v, i) => s + v * b[i], 0);
  const perTet = new Map<number, [number, number]>();
  for (let triangle = 0; triangle < cut.tetrahedron.length; triangle++) {
    const tet = cut.tetrahedron[triangle];
    let velocity = perTet.get(tet);
    if (!velocity) {
      const indices = Array.from(domain.tetrahedra.subarray(tet * 4, tet * 4 + 4));
      const xyz = indices.map((i) => Array.from(domain.points.subarray(i * 3, i * 3 + 3)).map((v) => v / 1000));
      const edges = xyz.slice(1).map((p) => p.map((v, i) => v - xyz[0][i]));
      const cofactors = [cross(edges[1], edges[2]), cross(edges[2], edges[0]), cross(edges[0], edges[1])];
      const det = dot(edges[0], cofactors[0]);
      if (!Number.isFinite(det) || det === 0) throw new Error('Degenerate velocity tetrahedron');
      const gradient = (field: Float32Array) => cofactors.reduce((sum, c, i) => sum + dot(c, normal) * (field[indices[i + 1]] - field[indices[0]]) / det, 0);
      const factor = 2 * Math.PI * frequencyHz * density;
      velocity = [gradient(values.imag) / factor, -gradient(values.real) / factor];
      perTet.set(tet, velocity);
    }
    cut.real.fill(velocity[0], triangle * 3, triangle * 3 + 3);
    cut.imag.fill(velocity[1], triangle * 3, triangle * 3 + 3);
  }
  return cut;
}
