import { Box3, Vector3 } from 'three';
import type { FrameScene } from './frameScene';
import type { ParsedMSH } from './mshParser';
import { ROLE_MATERIAL_FAMILY, SOURCE_ROLES } from './types';
import type { SceneSurface, SourceRole } from './types';
import { expandImportedSymmetry, markParametricSolvedDomain, quadrantsForCutPlanes } from './symmetryScene';

let nextArtifactToken = 1;

export interface ImportedMeshScene {
  name: string;
  source: 'cad' | 'file';
  /** Present for CAD-return scenes so the solve command can prove that the
   * visible mesh and the immutable ingestion record are the same artifact. */
  ingestId: string | null;
  scene: FrameScene;
  triangleCount: number;
  solvedTriangleCount: number;
  physicalGroupCount: number;
  /** Changes whenever the underlying artifact changes, even if its triangle
   * count and display name happen to match an earlier scene. */
  artifactToken: string;
}

export interface ImportedMeshSceneOptions {
  /** The supplied mesh already covers the complete display domain. */
  fullDomain?: boolean;
  /** Actual solver-domain count when the supplied visual artifact is separate. */
  solvedTriangleCount?: number;
  /** Stable digest/token supplied by the artifact owner when available. */
  artifactToken?: string;
}

interface CreaseSplitGeometry {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
}

/**
 * Build a drawable buffer for one physical group whose vertex normals average
 * across smooth joins only. Averaging every triangle that touches a vertex
 * smears a chamfer band into the faces beside it; here two triangles share a
 * normal only when the edge between them stays below the crease angle, so the
 * chamfer keeps its hard edges and the curved parts stay smooth.
 *
 * Adjacency is resolved on positions welded to a quantized grid, the same way
 * the edge extractor in frameScene does it: solver meshes routinely duplicate
 * vertices along a seam, and index equality alone would read every one of
 * those seams as a crease.
 */
function splitCreaseNormals(vertices: Float32Array, indices: Uint32Array): CreaseSplitGeometry {
  const vertexCount = Math.floor(vertices.length / 3);
  const weldedVertices = new Map<string, number>();
  const weldedIds = new Int32Array(vertexCount).fill(-1);
  const weldedIdFor = (vertex: number): number => {
    if (weldedIds[vertex] >= 0) return weldedIds[vertex];
    const offset = vertex * 3;
    const key = [
      Math.round(vertices[offset] / WELD_GRID),
      Math.round(vertices[offset + 1] / WELD_GRID),
      Math.round(vertices[offset + 2] / WELD_GRID),
    ].join(',');
    let weldedId = weldedVertices.get(key);
    if (weldedId === undefined) {
      weldedId = weldedVertices.size;
      weldedVertices.set(key, weldedId);
    }
    weldedIds[vertex] = weldedId;
    return weldedId;
  };

  // Corners are numbered face * 3 + k; a face contributes an area-weighted
  // normal to every corner group it lands in, and a unit normal for the
  // tangency test.
  const cornerVertices: number[] = [];
  const cornerWelded: number[] = [];
  const areaNormals: number[] = [];
  const unitNormals: number[] = [];
  for (let offset = 0; offset + 2 < indices.length; offset += 3) {
    const a = indices[offset];
    const b = indices[offset + 1];
    const c = indices[offset + 2];
    if (a >= vertexCount || b >= vertexCount || c >= vertexCount) continue;
    const ia = a * 3;
    const ib = b * 3;
    const ic = c * 3;
    const abx = vertices[ib] - vertices[ia];
    const aby = vertices[ib + 1] - vertices[ia + 1];
    const abz = vertices[ib + 2] - vertices[ia + 2];
    const acx = vertices[ic] - vertices[ia];
    const acy = vertices[ic + 1] - vertices[ia + 1];
    const acz = vertices[ic + 2] - vertices[ia + 2];
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    const length = Math.hypot(nx, ny, nz);
    const scale = Number.isFinite(length) && length > 0 ? 1 / length : 0;
    cornerVertices.push(a, b, c);
    cornerWelded.push(weldedIdFor(a), weldedIdFor(b), weldedIdFor(c));
    areaNormals.push(nx, ny, nz);
    unitNormals.push(nx * scale, ny * scale, nz * scale);
  }

  const cornerCount = cornerVertices.length;
  const parent = new Uint32Array(cornerCount);
  for (let corner = 0; corner < cornerCount; corner += 1) parent[corner] = corner;
  const find = (corner: number): number => {
    let root = corner;
    while (parent[root] !== root) root = parent[root];
    let walk = corner;
    while (parent[walk] !== root) {
      const next = parent[walk];
      parent[walk] = root;
      walk = next;
    }
    return root;
  };
  const union = (left: number, right: number) => {
    const a = find(left);
    const b = find(right);
    if (a !== b) parent[Math.max(a, b)] = Math.min(a, b);
  };

  // Each welded edge collects the corner pair every incident face contributes,
  // ordered low welded id first so the two faces' endpoints line up.
  const edges = new Map<string, number[]>();
  for (let face = 0; face < cornerCount; face += 3) {
    for (let step = 0; step < 3; step += 1) {
      const first = face + step;
      const second = face + (step + 1) % 3;
      const weldedFirst = cornerWelded[first];
      const weldedSecond = cornerWelded[second];
      if (weldedFirst === weldedSecond) continue;
      const low = Math.min(weldedFirst, weldedSecond);
      const key = `${low}:${Math.max(weldedFirst, weldedSecond)}`;
      const pair = weldedFirst === low ? [first, second] : [second, first];
      const entry = edges.get(key);
      if (entry) entry.push(pair[0], pair[1]);
      else edges.set(key, pair);
    }
  }
  for (const entry of edges.values()) {
    for (let first = 0; first + 1 < entry.length; first += 2) {
      for (let second = first + 2; second + 1 < entry.length; second += 2) {
        const faceFirst = Math.floor(entry[first] / 3) * 3;
        const faceSecond = Math.floor(entry[second] / 3) * 3;
        const dot = unitNormals[faceFirst] * unitNormals[faceSecond]
          + unitNormals[faceFirst + 1] * unitNormals[faceSecond + 1]
          + unitNormals[faceFirst + 2] * unitNormals[faceSecond + 2];
        if (dot < CREASE_COSINE) continue;
        union(entry[first], entry[second]);
        union(entry[first + 1], entry[second + 1]);
      }
    }
  }

  const outputIds = new Int32Array(cornerCount).fill(-1);
  const positions: number[] = [];
  const normals: number[] = [];
  const splitIndices = new Uint32Array(cornerCount);
  for (let corner = 0; corner < cornerCount; corner += 1) {
    const root = find(corner);
    let id = outputIds[root];
    if (id < 0) {
      id = positions.length / 3;
      outputIds[root] = id;
      const source = cornerVertices[root] * 3;
      positions.push(vertices[source], vertices[source + 1], vertices[source + 2]);
      normals.push(0, 0, 0);
    }
    splitIndices[corner] = id;
    const face = Math.floor(corner / 3) * 3;
    normals[id * 3] += areaNormals[face];
    normals[id * 3 + 1] += areaNormals[face + 1];
    normals[id * 3 + 2] += areaNormals[face + 2];
  }
  for (let offset = 0; offset < normals.length; offset += 3) {
    const length = Math.hypot(normals[offset], normals[offset + 1], normals[offset + 2]) || 1;
    normals[offset] /= length;
    normals[offset + 1] /= length;
    normals[offset + 2] /= length;
  }
  return {
    positions: Float32Array.from(positions),
    normals: Float32Array.from(normals),
    indices: splitIndices,
  };
}

/**
 * Physical names on a WG-ingested CAD return are a pipe-delimited record:
 *
 *   wg-import-v1|tag=2|source_id=source-hf|instance_id=…|role=HF
 *   wg-import-v1|rigid
 *
 * so the paint role the user chose in Fusion — the one that made that face red,
 * amber or blue there — is already on the mesh and needs no second lookup.
 */
function parseImportRecord(name: string): Map<string, string> | null {
  if (!name.startsWith('wg-import-v1|')) return null;
  const fields = new Map<string, string>();
  for (const field of name.split('|').slice(1)) {
    const split = field.indexOf('=');
    if (split < 0) fields.set(field, '');
    else fields.set(field.slice(0, split), field.slice(split + 1));
  }
  return fields;
}

const ROLE_BY_SOLVER_GROUP: Record<string, SourceRole> = {
  // The parametric mesher's own physical-group contract, seen when a solver
  // artifact is opened from the design file menu rather than ingested.
  SD1D1001: 'HF',
  mid_port_exit_left: 'PORT_EXIT',
  mid_port_exit_right: 'PORT_EXIT',
};

function sourceRoleFor(label: string): SourceRole | null {
  const upper = label.toUpperCase();
  if ((SOURCE_ROLES as readonly string[]).includes(upper)) return upper as SourceRole;
  return ROLE_BY_SOLVER_GROUP[label] ?? null;
}

interface ImportedGroup {
  role: string;
  sourceRole: SourceRole | null;
}

/** Name and classify one physical group. Sources are labelled by the role the
 * CAD document gave them, keeping the two-driver case readable — `HF`, `LF` —
 * where the raw record would read as one unbroken eighty-character string. */
function describeTag(tag: number, physicalNames: Map<number, string>): ImportedGroup {
  const raw = physicalNames.get(tag)?.trim() ?? '';
  const record = parseImportRecord(raw);
  const declared = record?.get('role');
  if (declared) {
    const sourceRole = sourceRoleFor(declared);
    // Two sources can share a role; `source-hf-2` keeps them apart on screen.
    const ordinal = /-(\d+)$/.exec(record?.get('source_id') ?? '')?.[1];
    const label = (sourceRole ?? declared) + (ordinal ? `-${ordinal}` : '');
    return { role: `imported.${label}`, sourceRole };
  }
  const name = (record ? [...record.keys()][0] ?? '' : raw).replace(/[^a-zA-Z0-9_.-]+/g, '-') || `tag-${tag}`;
  return { role: `imported.${name}`, sourceRole: sourceRoleFor(name) };
}

export function createImportedMeshScene(
  name: string,
  mesh: ParsedMSH,
  source: ImportedMeshScene['source'] = 'file',
  ingestId: string | null = null,
  symmetryCutPlanes: readonly string[] = [],
  options: ImportedMeshSceneOptions = {},
): ImportedMeshScene {
  if (mesh.indices.length === 0) throw new Error('The MSH file contains no triangle elements');
  const grouped = new Map<number, number[]>();
  for (let triangle = 0; triangle < mesh.physicalTags.length; triangle += 1) {
    const tag = mesh.physicalTags[triangle];
    const indices = grouped.get(tag) ?? [];
    indices.push(mesh.indices[triangle * 3], mesh.indices[triangle * 3 + 1], mesh.indices[triangle * 3 + 2]);
    grouped.set(tag, indices);
  }
  const surfaces: SceneSurface[] = [...grouped.entries()].map(([tag, values]) => {
    const geometry = splitCreaseNormals(mesh.vertices, Uint32Array.from(values));
    const group = describeTag(tag, mesh.physicalNames);
    return {
      key: `msh:${name}:${tag}`,
      role: group.role,
      shading: 'smooth',
      materialClass: group.sourceRole
        ? (`${ROLE_MATERIAL_FAMILY[group.sourceRole]}-smooth` as const)
        : ('horn-smooth' as const),
      sourceRole: group.sourceRole,
      enclosure: false,
      positions: geometry.positions,
      normals: geometry.normals,
      indices: geometry.indices,
      curvature: null,
    };
  });
  const bounds = new Box3();
  const point = new Vector3();
  for (let offset = 0; offset < mesh.vertices.length; offset += 3) {
    bounds.expandByPoint(point.set(mesh.vertices[offset], mesh.vertices[offset + 1], mesh.vertices[offset + 2]));
  }
  if (bounds.isEmpty()) bounds.set(new Vector3(-1, -1, -1), new Vector3(1, 1, 1));
  const solvedScene = { surfaces, bounds, unitsPerMetre: 1 as const, hasCurvature: false };
  const scene = options.fullDomain
    ? markParametricSolvedDomain(solvedScene, quadrantsForCutPlanes(symmetryCutPlanes))
    : expandImportedSymmetry(solvedScene, symmetryCutPlanes);
  return {
    name,
    source,
    ingestId,
    scene,
    triangleCount: scene.surfaces.reduce((count, surface) => count + surface.indices.length / 3, 0),
    solvedTriangleCount: options.solvedTriangleCount ?? mesh.indices.length / 3,
    physicalGroupCount: grouped.size,
    artifactToken: options.artifactToken ?? `imported-${nextArtifactToken++}`,
  };
}

const WELD_GRID = 1e-4;
export const CREASE_ANGLE_DEGREES = 30;
const CREASE_COSINE = Math.cos(CREASE_ANGLE_DEGREES * Math.PI / 180);
