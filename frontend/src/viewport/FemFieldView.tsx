import { useEffect, useMemo } from 'react';
import { BufferAttribute, BufferGeometry, DoubleSide } from 'three';
import {
  fieldPlaneWindowForMode,
  maxFieldSplDb,
  type FieldPlaneDisplayMode,
  type FieldPlaneValueWindow,
} from './fieldPlaneColor';
import {
  clippedGhostEdges,
  elementMeanSlice,
  FEM_GHOST_STYLE,
  FEM_ROLE_COLORS,
  femFieldColors,
  sliceFemField,
  type ComplexNodal,
  type FemFieldDomain,
  type FemSurfaceKind,
  type SlicePlane,
} from './femField';

export type FemFieldRendering = 'smooth' | 'element';

export interface FemFieldGeometries {
  /** One geometry per boundary kind, shown only while no slice is active. */
  surfaces: Array<{ kind: FemSurfaceKind; geometry: BufferGeometry }>;
  slice: BufferGeometry | null;
  ghost: BufferGeometry | null;
  window: FieldPlaneValueWindow | null;
}

/** Build every three.js geometry the FEM view draws, in millimetres. */
export function buildFemFieldGeometries(
  domain: FemFieldDomain,
  values: ComplexNodal,
  options: {
    plane: SlicePlane | null;
    mode: FieldPlaneDisplayMode;
    rendering: FemFieldRendering;
    sequential: readonly string[];
    phaseRadians?: number;
  },
): FemFieldGeometries {
  const surfaces: FemFieldGeometries['surfaces'] = [];
  if (options.plane === null) {
    const byKind = new Map<FemSurfaceKind, number[]>();
    for (const surface of domain.surfaces) {
      const list = byKind.get(surface.kind) ?? [];
      for (const vertex of surface.triangles) list.push(vertex);
      byKind.set(surface.kind, list);
    }
    for (const [kind, indices] of byKind) {
      const geometry = new BufferGeometry();
      geometry.setAttribute('position', new BufferAttribute(domain.points, 3));
      geometry.setIndex(indices);
      geometry.computeVertexNormals();
      surfaces.push({ kind, geometry });
    }
    return { surfaces, slice: null, ghost: null, window: null };
  }
  const cut = sliceFemField(domain, values, options.plane);
  const shown = options.rendering === 'element' ? elementMeanSlice(domain, values, cut) : cut;
  // The window comes from the nodal field, not the slice, so moving the plane
  // does not rescale the colours under the user.
  const window = fieldPlaneWindowForMode(options.mode, values.real, values.imag);
  const { colors } = femFieldColors(shown, options.mode, {
    sequential: options.sequential,
    phaseRadians: options.phaseRadians,
    window,
    fieldMaxSplDb: maxFieldSplDb(values.real, values.imag),
  });
  const slice = new BufferGeometry();
  slice.setAttribute('position', new BufferAttribute(cut.positions, 3));
  slice.setAttribute('color', new BufferAttribute(colors, 3));
  const ghost = new BufferGeometry();
  ghost.setAttribute('position', new BufferAttribute(clippedGhostEdges(domain, options.plane), 3));
  return { surfaces, slice, ghost, window };
}

interface FemFieldViewProps {
  domain: FemFieldDomain;
  values: ComplexNodal;
  plane: SlicePlane | null;
  mode: FieldPlaneDisplayMode;
  rendering: FemFieldRendering;
  sequential: readonly string[];
  /** Scene units per metre; the FEM domain is in millimetres. */
  unitsPerMetre: number;
  phaseRadians?: number;
}

/**
 * A declared FEM region and its solved interior field, drawn the way Boundary
 * Lab draws an interior observation plane: role-coloured walls when nothing is
 * cut, and once a slice plane is set, the coloured cut plus a faint ghost of
 * the kept half-volume with the walls hidden.
 */
export function FemFieldView({
  domain, values, plane, mode, rendering, sequential, unitsPerMetre, phaseRadians,
}: FemFieldViewProps) {
  const built = useMemo(
    () => buildFemFieldGeometries(domain, values, { plane, mode, rendering, sequential, phaseRadians }),
    [domain, values, plane, mode, rendering, sequential, phaseRadians],
  );
  useEffect(() => () => {
    for (const surface of built.surfaces) surface.geometry.dispose();
    built.slice?.dispose();
    built.ghost?.dispose();
  }, [built]);
  const scale = unitsPerMetre / 1000;
  return (
    <group scale={[scale, scale, scale]} name={`fem-region:${domain.regionId}`}>
      {built.surfaces.map(({ kind, geometry }) => (
        <group key={kind}>
          <mesh geometry={geometry} name={`fem-surface:${kind}`}>
            <meshStandardMaterial color={FEM_ROLE_COLORS[kind].fill} flatShading side={DoubleSide} />
          </mesh>
          <mesh geometry={geometry}>
            <meshBasicMaterial color={FEM_ROLE_COLORS[kind].edge} wireframe />
          </mesh>
        </group>
      ))}
      {built.slice && (
        <mesh geometry={built.slice} name="fem-slice">
          <meshBasicMaterial vertexColors side={DoubleSide} transparent opacity={0.92} />
        </mesh>
      )}
      {built.ghost && (
        <lineSegments geometry={built.ghost} name="fem-ghost" raycast={() => null}>
          <lineBasicMaterial color={FEM_GHOST_STYLE.edge} transparent opacity={FEM_GHOST_STYLE.opacity * 3} />
        </lineSegments>
      )}
    </group>
  );
}
