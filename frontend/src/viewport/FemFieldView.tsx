import { useEffect, useMemo, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { BufferAttribute, BufferGeometry, DoubleSide } from 'three';
import {
  advanceFieldPlanePhase,
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
  femNormalVelocitySlice,
  sliceFemField,
  type ComplexNodal,
  type FemSlice,
  type FemFieldDomain,
  type FemSurfaceKind,
  type SlicePlane,
} from './femField';

export type FemDisplayMode = FieldPlaneDisplayMode | 'real' | 'imaginary';
export type FemFieldRendering = 'smooth' | 'element';

export interface FemFieldGeometries {
  /** One geometry per boundary kind, shown only while no slice is active. */
  surfaces: Array<{ kind: FemSurfaceKind; geometry: BufferGeometry }>;
  slice: BufferGeometry | null;
  ghost: BufferGeometry | null;
  window: FieldPlaneValueWindow | null;
  fieldMaxSplDb: number;
}

/** Build every three.js geometry the FEM view draws, in millimetres. */
export function buildFemFieldGeometries(
  domain: FemFieldDomain,
  values: ComplexNodal,
  options: {
    plane: SlicePlane | null;
    mode: FemDisplayMode;
    includeInterior?: boolean;
    planeField?: import('../api/femField').FemPlaneField | null;
    manualWindow?: FieldPlaneValueWindow | null;
    interiorSlice?: FemSlice;
    velocity?: { frequencyHz: number; density: number };
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
    return { surfaces, slice: null, ghost: null, window: null, fieldMaxSplDb: 0 };
  }
  const mode = options.mode === 'real' || options.mode === 'imaginary' ? 'instantaneous' : options.mode;
  const phaseRadians = options.mode === 'real' ? 0 : options.mode === 'imaginary' ? Math.PI / 2 : options.phaseRadians;
  const empty = { positions: new Float32Array(0), real: new Float32Array(0),
    imag: new Float32Array(0), tetrahedron: new Uint32Array(0) };
  const interior = options.planeField && !options.includeInterior ? empty : options.interiorSlice ?? (options.velocity
    ? femNormalVelocitySlice(domain, values, options.plane, options.velocity.frequencyHz, options.velocity.density)
    : sliceFemField(domain, values, options.plane));
  const interiorShown = !options.velocity && options.rendering === 'element'
    ? elementMeanSlice(domain, values, interior) : interior;
  const join = (a: Float32Array, b: Float32Array) => {
    const joined = new Float32Array(a.length + b.length); joined.set(a); joined.set(b, a.length); return joined;
  };
  const exterior = options.planeField;
  // The exterior grid comes first: its cells across an aperture overlap the
  // exact interior cut by at most a cell, and the later interior cut wins.
  const cut = exterior ? {
    positions: options.includeInterior ? join(exterior.positions, interior.positions) : exterior.positions,
    real: options.includeInterior ? join(exterior.real, interiorShown.real) : exterior.real,
    imag: options.includeInterior ? join(exterior.imag, interiorShown.imag) : exterior.imag,
    tetrahedron: new Uint32Array(0),
  } : interior;
  const shown = exterior ? cut : interiorShown;
  // Pressure uses all retained interior nodes, keeping the colour range
  // stable as the cut moves. Velocity uses the exact element values on the cut.
  const reference = options.velocity ? shown : exterior
    ? options.includeInterior ? { real: join(values.real, exterior.real), imag: join(values.imag, exterior.imag) } : exterior
    : values;
  const window = options.manualWindow ?? fieldPlaneWindowForMode(mode, reference.real, reference.imag);
  const fieldMaxSplDb = maxFieldSplDb(reference.real, reference.imag);
  const { colors } = femFieldColors(shown, mode, {
    sequential: options.sequential,
    phaseRadians,
    window,
    fieldMaxSplDb,
  });
  const slice = new BufferGeometry();
  slice.setAttribute('position', new BufferAttribute(cut.positions, 3));
  slice.setAttribute('color', new BufferAttribute(colors, 3));
  slice.setAttribute('pressureReal', new BufferAttribute(shown.real, 1));
  slice.setAttribute('pressureImag', new BufferAttribute(shown.imag, 1));
  const ghost = new BufferGeometry();
  ghost.setAttribute('position', new BufferAttribute(clippedGhostEdges(domain, options.plane), 3));
  return { surfaces, slice, ghost, window, fieldMaxSplDb };
}

interface FemFieldViewProps {
  domain: FemFieldDomain;
  values: ComplexNodal;
  plane: SlicePlane | null;
  mode: FemDisplayMode;
  includeInterior?: boolean;
  planeField?: import('../api/femField').FemPlaneField | null;
  manualWindow?: FieldPlaneValueWindow | null;
  interiorSlice?: FemSlice;
  velocity?: { frequencyHz: number; density: number };
  rendering: FemFieldRendering;
  sequential: readonly string[];
  /** Scene units per metre; the FEM domain is in millimetres. */
  unitsPerMetre: number;
  phaseRadians?: number;
  animating?: boolean;
  animationSpeed?: number;
}

/**
 * A declared FEM region and its solved interior field, drawn the way Boundary
 * Lab draws an interior observation plane: role-coloured walls when nothing is
 * cut, and once a slice plane is set, the coloured cut plus a faint ghost of
 * the kept half-volume with the walls hidden.
 */
export function FemFieldView({
  domain, values, plane, mode, planeField, includeInterior, manualWindow, velocity, interiorSlice, rendering, sequential, unitsPerMetre, phaseRadians, animating = false, animationSpeed = 1,
}: FemFieldViewProps) {
  const built = useMemo(
    () => buildFemFieldGeometries(domain, values, { plane, mode, planeField, includeInterior, manualWindow, velocity, interiorSlice, rendering, sequential, phaseRadians }),
    [domain, values, plane, mode, planeField, includeInterior, manualWindow, velocity, interiorSlice, rendering, sequential, phaseRadians],
  );
  const invalidate = useThree((state) => state.invalidate);
  const phase = useRef(0);
  const fieldMaxSplDb = built.fieldMaxSplDb;
  // Animation recolours the existing cut; it never recuts tetrahedra or
  // rebuilds ghost geometry on every frame.
  const recolor = (phaseRadians: number) => {
    if (!built.slice || !built.window) return;
    const shown = {
      real: built.slice.getAttribute('pressureReal').array as Float32Array,
      imag: built.slice.getAttribute('pressureImag').array as Float32Array,
    };
    const { colors } = femFieldColors(shown, mode === 'real' || mode === 'imaginary' ? 'instantaneous' : mode, {
      sequential, phaseRadians: mode === 'real' ? 0 : mode === 'imaginary' ? Math.PI / 2 : phaseRadians, window: built.window,
      fieldMaxSplDb,
    });
    const attribute = built.slice.getAttribute('color');
    attribute.array.set(colors);
    attribute.needsUpdate = true;
  };
  useFrame((_state, delta) => {
    if (!animating || mode === 'real' || mode === 'imaginary') return;
    phase.current = advanceFieldPlanePhase(phase.current, delta, animationSpeed);
    recolor(phase.current);
    invalidate();
  });
  useEffect(() => {
    phase.current = 0;
    recolor(phaseRadians ?? 0);
    invalidate();
  }, [built, animating, animationSpeed, invalidate]);
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
