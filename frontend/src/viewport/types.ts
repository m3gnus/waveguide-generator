import type { BufferGeometry, Material } from 'three';

export type DisplayMode = 'clay' | 'solid-wire' | 'wireframe' | 'xray' | 'zebra' | 'curvature' | 'edges';
export type CameraPreset = 'front' | 'three-quarter' | 'top';
export type SurfaceMaterialClass = 'horn-smooth' | 'horn-flat' | 'boundary-smooth' | 'boundary-flat' | 'enclosure-smooth' | 'enclosure-flat';

export interface SceneSurface {
  key: string;
  role: string;
  shading: 'smooth' | 'flat';
  materialClass: SurfaceMaterialClass;
  enclosure: boolean;
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  curvature: Float32Array | null;
}

export interface MaterialLibrary {
  surfaces: Record<SurfaceMaterialClass, Material>;
  wire: Material;
  edge: Material;
  stencilBack: Material;
  stencilFront: Material;
  cap: Material;
  all: Material[];
}

export interface ManagedSurfaceGeometry {
  geometry: BufferGeometry;
  dispose(): void;
}
