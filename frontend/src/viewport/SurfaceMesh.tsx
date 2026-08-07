import { useEffect, useMemo } from 'react';
import { BufferAttribute, BufferGeometry } from 'three';
import { SurfaceBufferManager } from './bufferManager';
import type { DemandRenderScheduler } from './demandRender';
import { curvatureColors, MAX_EDGE_TRIANGLES, surfaceBoundaryPositions } from './frameScene';
import type { DisplayMode, MaterialLibrary, SceneSurface } from './types';

export interface SurfaceMeshProps {
  surface: SceneSurface;
  mode: DisplayMode;
  visible: boolean;
  sectionCut: boolean;
  materials: MaterialLibrary;
  scheduler: DemandRenderScheduler;
}

export function SurfaceMesh({ surface, mode, visible, sectionCut, materials, scheduler }: SurfaceMeshProps) {
  const manager = useMemo(() => new SurfaceBufferManager(), []);
  const colors = useMemo(
    () => mode === 'curvature' && surface.curvature ? curvatureColors(surface.curvature) : null,
    [mode, surface.curvature],
  );
  // Keyed on the typed arrays, not on `surface`. Every decoded preview frame
  // builds fresh SceneSurface objects, so keying on the object meant this memo
  // never hit and the boundary extraction -- a per-vertex string key, a Map
  // over 3N edges, and an O(normals) feature test per edge -- re-ran at up to
  // 30 Hz during a drag. The arrays are the stable-by-content identity: new
  // geometry always means new buffers.
  const boundary = useMemo(() => {
    if (mode !== 'edges') return null;
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(surfaceBoundaryPositions(surface), 3));
    return geometry;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- positions/indices identify the surface's geometry
  }, [mode, surface.positions, surface.indices, surface.normals]);
  const material = materials.surfaces[surface.materialClass];
  const edgeFallback = mode === 'edges' && Math.floor(surface.indices.length / 3) > MAX_EDGE_TRIANGLES;
  const edgeFillMaterial = useMemo(() => {
    if (!edgeFallback) return null;
    const fallback = material.clone();
    fallback.colorWrite = true;
    fallback.polygonOffset = false;
    return fallback;
  }, [edgeFallback, material]);

  useEffect(() => scheduler.schedule(() => {
    manager.update({
      positions: surface.positions,
      normals: surface.normals,
      indices: surface.indices,
      colors,
    });
  }), [colors, manager, scheduler, surface.indices, surface.normals, surface.positions]);

  useEffect(() => () => manager.dispose(), [manager]);
  useEffect(() => () => boundary?.dispose(), [boundary]);
  useEffect(() => () => edgeFillMaterial?.dispose(), [edgeFillMaterial]);

  // Curvature data is optional even at fine LOD. Keep the neutral material
  // visible when it is absent so switching inspection modes never blanks the
  // model; Viewport explains that only the analytic heatmap is unavailable.
  return <group visible={visible}>
    {mode !== 'edges' && <mesh geometry={manager.geometry} material={material} renderOrder={mode === 'xray' ? 10 : 1} />}
    {mode === 'edges' && <mesh geometry={manager.geometry} material={material} renderOrder={1} />}
    {edgeFillMaterial && <mesh geometry={manager.geometry} material={edgeFillMaterial} renderOrder={2} />}
    {mode === 'solid-wire' && <mesh geometry={manager.geometry} material={materials.wire} renderOrder={2} />}
    {mode === 'edges' && boundary && <lineSegments geometry={boundary} material={materials.edge} renderOrder={3} />}
    {sectionCut && <>
      <mesh geometry={manager.geometry} material={materials.stencilBack} renderOrder={100} />
      <mesh geometry={manager.geometry} material={materials.stencilFront} renderOrder={101} />
    </>}
  </group>;
}
