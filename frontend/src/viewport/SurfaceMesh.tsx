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
  // Keyed on the typed arrays rather than on `surface`, which is a fresh object
  // per decoded frame. Be clear about what that buys and what it does not: the
  // WebSocket decoder allocates new typed arrays for every frame too, so a
  // frame carrying new geometry always misses this memo and always pays for the
  // extraction. What it avoids is re-extracting when the component re-renders
  // for some *other* reason -- a mode toggle, an enclosure toggle, a parent
  // render -- while the geometry has not moved.
  //
  // A hidden surface is skipped outright. Nothing draws its lines, and on a box
  // design with the enclosure switched off that is a fifth of the triangles in
  // the scene extracted for nobody.
  const boundary = useMemo(() => {
    if (mode !== 'edges' || !visible) return null;
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(surfaceBoundaryPositions(surface), 3));
    return geometry;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- positions/indices identify the surface's geometry
  }, [mode, visible, surface.positions, surface.indices, surface.normals]);
  const material = (surface.solvedDomain ? materials.solvedSurfaces : materials.surfaces)[surface.materialClass];
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
