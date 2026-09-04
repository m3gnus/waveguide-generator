import type { Material } from 'three';

export type DisplayMode = 'clay' | 'solid-wire' | 'wireframe' | 'xray' | 'zebra' | 'curvature' | 'normals' | 'edges';
export type CameraPreset = 'front' | 'three-quarter' | 'top';
export type ViewportTheme = 'dark' | 'light';
/** The paint roles WGLink recognises on an acoustic face, in Fusion's own
 * vocabulary. They travel to the viewport inside the imported mesh's physical
 * names, and the parametric source cap is the HF driver by construction. */
export type SourceRole = 'HF' | 'MF' | 'LF' | 'PORT_EXIT';
export const SOURCE_ROLES: readonly SourceRole[] = ['HF', 'MF', 'LF', 'PORT_EXIT'];

/** One material family per distinguishable surface kind. The four role
 * families carry the Fusion appearance colours; `source` is the neutral cap
 * material they fall back to when role colouring is switched off. */
export type SurfaceMaterialFamily = 'horn' | 'source' | 'enclosure' | 'hf' | 'mf' | 'lf' | 'port';
export type SurfaceMaterialClass = `${SurfaceMaterialFamily}-smooth` | `${SurfaceMaterialFamily}-flat`;

export const ROLE_MATERIAL_FAMILY: Record<SourceRole, SurfaceMaterialFamily> = {
  HF: 'hf', MF: 'mf', LF: 'lf', PORT_EXIT: 'port',
};

export interface SceneSurface {
  key: string;
  role: string;
  shading: 'smooth' | 'flat';
  materialClass: SurfaceMaterialClass;
  /** Set only on a radiating surface, so the viewport can name and colour it
   * the way the CAD document does. */
  sourceRole?: SourceRole | null;
  enclosure: boolean;
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  curvature: Float32Array | null;
  /** True only for triangles in the physical mesh domain sent to the solver.
   * Reflected/full-model display copies remain false. */
  solvedDomain?: boolean;
}

export interface MaterialLibrary {
  surfaces: Record<SurfaceMaterialClass, Material>;
  solvedSurfaces: Record<SurfaceMaterialClass, Material>;
  wire: Material;
  edge: Material;
  stencilBack: Material;
  stencilFront: Material;
  cap: Material;
  /** Back-face paint for the far wall of an open section cut. Null in the modes
   * that already draw or deliberately withhold their back faces. */
  interior: Material | null;
  all: Material[];
}
