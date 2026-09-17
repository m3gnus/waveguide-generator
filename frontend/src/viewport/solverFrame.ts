import type { ParsedMSH } from './mshParser';

/** The solver frame of an unlinked (CAD-authored) model: the assembly axis it
 * radiates along. The matrices are the server's (`server/cadlink/solver_frame.py`,
 * pinned equal to `solverFrame.fixture.json` on both sides); this module only
 * applies the ones the server hands out, never makes its own. */
export const SOLVER_FRAME_AXES = ['+z', '-z', '+x', '-x', '+y', '-y'] as const;
export type SolverFrameAxis = typeof SOLVER_FRAME_AXES[number];

/** A 4x4 rigid matrix, row-major: `p' = M · [x, y, z, 1]`. */
export type RowMajorMatrix = number[][];

export function applyRowMajor(matrix: RowMajorMatrix, x: number, y: number, z: number): [number, number, number] {
  return [0, 1, 2].map((row) => matrix[row][0] * x + matrix[row][1] * y + matrix[row][2] * z + (matrix[row][3] ?? 0)) as [number, number, number];
}

export interface FramePreviewView {
  name: 'side' | 'top';
  /** Horizontal is always the solver +Z, the radiation axis. */
  horizontal: 'z';
  vertical: 'y' | 'x';
  /** Six numbers per triangle: (h, v) for each corner. */
  triangles: Float32Array;
  /** 1 where the triangle belongs to a drive source. */
  source: Uint8Array;
}

export interface FramePreviewProjection {
  views: FramePreviewView[];
  /** [horizontal, vertical] extents over both views. */
  bounds: { min: [number, number]; max: [number, number] };
}

const SOURCE_ROLES = /^(LF|MF|HF|PORT_EXIT|PASSIVE_CARDIOID)$/i;

function isSourceGroup(name: string | undefined): boolean {
  if (!name) return false;
  return name.includes('|source_id=') || SOURCE_ROLES.test(name.trim());
}

/** The model in the solver frame a matrix names, as two orthographic views
 * along the radiation axis: from the side (solver Y up) and from the top
 * (solver X up). */
export function framePreviewProjection(mesh: ParsedMSH, matrix: RowMajorMatrix): FramePreviewProjection {
  const vertexCount = mesh.vertices.length / 3;
  const solver = new Float64Array(mesh.vertices.length);
  for (let index = 0; index < vertexCount; index += 1) {
    const offset = index * 3;
    const [x, y, z] = applyRowMajor(matrix, mesh.vertices[offset], mesh.vertices[offset + 1], mesh.vertices[offset + 2]);
    solver[offset] = x;
    solver[offset + 1] = y;
    solver[offset + 2] = z;
  }
  const triangleCount = mesh.indices.length / 3;
  const source = new Uint8Array(triangleCount);
  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    source[triangle] = isSourceGroup(mesh.physicalNames.get(mesh.physicalTags[triangle])) ? 1 : 0;
  }
  const min: [number, number] = [Infinity, Infinity];
  const max: [number, number] = [-Infinity, -Infinity];
  const view = (name: 'side' | 'top', verticalComponent: 0 | 1): FramePreviewView => {
    const triangles = new Float32Array(triangleCount * 6);
    for (let corner = 0; corner < mesh.indices.length; corner += 1) {
      const vertex = mesh.indices[corner] * 3;
      const h = solver[vertex + 2];
      const v = solver[vertex + verticalComponent];
      triangles[corner * 2] = h;
      triangles[corner * 2 + 1] = v;
      min[0] = Math.min(min[0], h); max[0] = Math.max(max[0], h);
      min[1] = Math.min(min[1], v); max[1] = Math.max(max[1], v);
    }
    return { name, horizontal: 'z', vertical: verticalComponent === 1 ? 'y' : 'x', triangles, source };
  };
  const views = [view('side', 1), view('top', 0)];
  if (!Number.isFinite(min[0])) return { views, bounds: { min: [0, 0], max: [0, 0] } };
  return { views, bounds: { min, max } };
}
