import {
  AlwaysStencilFunc,
  BackSide,
  Color,
  DecrementWrapStencilOp,
  DoubleSide,
  FrontSide,
  IncrementWrapStencilOp,
  KeepStencilOp,
  LineBasicMaterial,
  MeshBasicMaterial,
  MeshStandardMaterial,
  NotEqualStencilFunc,
  Plane,
  ReplaceStencilOp,
  ShaderMaterial,
} from 'three';
import type { DisplayMode, MaterialLibrary, SurfaceMaterialClass, SurfaceMaterialFamily, ViewportTheme } from './types';

const families: SurfaceMaterialFamily[] = ['horn', 'source', 'enclosure', 'hf', 'mf', 'lf', 'port'];
const classes: SurfaceMaterialClass[] = families.flatMap(
  (family): SurfaceMaterialClass[] => [`${family}-smooth`, `${family}-flat`],
);

/** The four families that carry a painted acoustic role rather than a
 * structural material. Switching role colouring off resolves all of them to
 * the neutral source cap instead. */
const roleFamilies = new Set<SurfaceMaterialFamily>(['hf', 'mf', 'lf', 'port']);

// Fallbacks only, for a document-less render; the tokens above them are what
// actually ships. They used to be blue-greys from the pre-Console skin, which
// meant a token lookup failure silently swapped the model to a palette the
// interface no longer contains. The role colours are Fusion's own, so both
// themes fall back to the same four.
const fallbackColors: Record<ViewportTheme, Record<SurfaceMaterialFamily, string>> = {
  dark: {
    horn: '#b8b1a6', source: '#c07a4e', enclosure: '#7a7367',
    hf: '#ff0000', mf: '#ffbb00', lf: '#004cff', port: '#449648',
  },
  light: {
    horn: '#9fa39b', source: '#a5674a', enclosure: '#8a8d85',
    hf: '#ff0000', mf: '#ffbb00', lf: '#004cff', port: '#449648',
  },
};

function tokenColor(token: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim() || fallback;
}

export function materialFamily(materialClass: SurfaceMaterialClass): SurfaceMaterialFamily {
  return materialClass.slice(0, materialClass.lastIndexOf('-')) as SurfaceMaterialFamily;
}

export function familyColor(family: SurfaceMaterialFamily, theme: ViewportTheme): Color {
  return new Color(tokenColor(`--vp-${family}-material`, fallbackColors[theme][family]));
}

function zebraMaterial(clippingPlanes: Plane[], flat: boolean): ShaderMaterial {
  return new ShaderMaterial({
    defines: flat ? { FLAT_SHADED: 1 } : {},
    clipping: clippingPlanes.length > 0,
    clippingPlanes,
    side: FrontSide,
    vertexShader: `
      #include <clipping_planes_pars_vertex>
      varying vec3 vWorldNormal;
      varying vec3 vWorldPosition;
      void main() {
        vec4 worldPosition = modelMatrix * vec4(position, 1.0);
        vec4 mvPosition = viewMatrix * worldPosition;
        vWorldPosition = worldPosition.xyz;
        vWorldNormal = normalize(mat3(modelMatrix) * normal);
        gl_Position = projectionMatrix * mvPosition;
        #include <clipping_planes_vertex>
      }
    `,
    fragmentShader: `
      #include <clipping_planes_pars_fragment>
      varying vec3 vWorldNormal;
      varying vec3 vWorldPosition;
      void main() {
        #include <clipping_planes_fragment>
        vec3 incident = normalize(vWorldPosition - cameraPosition);
        vec3 shadingNormal = normalize(vWorldNormal);
        #ifdef FLAT_SHADED
          shadingNormal = normalize(cross(dFdx(vWorldPosition), dFdy(vWorldPosition)));
          if (!gl_FrontFacing) shadingNormal = -shadingNormal;
        #endif
        vec3 reflected = reflect(incident, shadingNormal);
        float band = smoothstep(0.38, 0.62, 0.5 + 0.5 * sin(reflected.y * 42.0));
        // Neutral-warm bands. A blue-white highlight over a blue-black shadow
        // read as a different material from every other mode in the viewport;
        // zebra is a reflection-continuity check and only needs the contrast.
        vec3 darkBand = vec3(0.036, 0.033, 0.030);
        vec3 lightBand = vec3(1.0, 0.972, 0.925);
        gl_FragColor = vec4(mix(darkBand, lightBand, band), 1.0);
      }
    `,
  });
}

/**
 * Normal inspection: front faces are tinted by the direction their shipped
 * normal points, back faces are flat magenta.
 *
 * Every other mode culls back faces, which is exactly why an inverted patch
 * reads as a hole rather than as a fault — the bug that made a rounded mouth
 * rim invisible. Drawing both sides and colouring them differently turns that
 * into something you can see and point at.
 */
function normalsMaterial(clippingPlanes: Plane[], flat: boolean): ShaderMaterial {
  return new ShaderMaterial({
    defines: flat ? { FLAT_SHADED: 1 } : {},
    clipping: clippingPlanes.length > 0,
    clippingPlanes,
    side: DoubleSide,
    vertexShader: `
      #include <clipping_planes_pars_vertex>
      varying vec3 vWorldNormal;
      varying vec3 vWorldPosition;
      void main() {
        vec4 worldPosition = modelMatrix * vec4(position, 1.0);
        vec4 mvPosition = viewMatrix * worldPosition;
        vWorldPosition = worldPosition.xyz;
        vWorldNormal = normalize(mat3(modelMatrix) * normal);
        gl_Position = projectionMatrix * mvPosition;
        #include <clipping_planes_vertex>
      }
    `,
    fragmentShader: `
      #include <clipping_planes_pars_fragment>
      varying vec3 vWorldNormal;
      varying vec3 vWorldPosition;
      void main() {
        #include <clipping_planes_fragment>
        vec3 shadingNormal = normalize(vWorldNormal);
        #ifdef FLAT_SHADED
          shadingNormal = normalize(cross(dFdx(vWorldPosition), dFdy(vWorldPosition)));
        #endif
        if (!gl_FrontFacing) {
          // Seeing this colour anywhere means a surface is facing away from the
          // acoustic domain — the geometry is wrong, not the render.
          gl_FragColor = vec4(0.85, 0.13, 0.52, 1.0);
          return;
        }
        gl_FragColor = vec4(shadingNormal * 0.5 + 0.5, 1.0);
      }
    `,
  });
}

function surfaceMaterial(
  mode: DisplayMode,
  materialClass: SurfaceMaterialClass,
  clippingPlanes: Plane[],
  theme: ViewportTheme,
  solvedTint = false,
  roleColors = true,
) {
  const declared = materialFamily(materialClass);
  // A role family with colouring switched off is the neutral cap material, so
  // nothing about the scene has to be rebuilt to turn the Fusion colours off.
  const family = roleColors || !roleFamilies.has(declared) ? declared : 'source';
  const color = familyColor(family, theme);
  // The solved-domain wash exists to separate a solved quadrant from its
  // mirrored copies. Laying it over a role colour would move the hue the CAD
  // document assigned, which is the one thing these four colours must not do.
  if (solvedTint && !roleFamilies.has(family)) {
    color.lerp(
      new Color(tokenColor('--vp-solved-domain-material', theme === 'light' ? '#a96b4d' : '#d58c62')),
      0.28,
    );
  }
  const flatShading = materialClass.endsWith('-flat');
  if (mode === 'wireframe') {
    return new MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.9, side: FrontSide, clippingPlanes });
  }
  if (mode === 'xray') {
    return new MeshStandardMaterial({
      color, roughness: 0.32, metalness: 0.06, transparent: true, opacity: 0.22,
      depthWrite: false, side: DoubleSide, clippingPlanes,
    });
  }
  if (mode === 'zebra') return zebraMaterial(clippingPlanes, flatShading);
  if (mode === 'normals') return normalsMaterial(clippingPlanes, flatShading);
  if (mode === 'curvature') {
    // Multiplies the vertex colours from curvatureColors, so it has to be a
    // near-neutral: the cold tint it used to carry pulled the whole curvature
    // ramp towards blue.
    return new MeshStandardMaterial({ color: '#f2ece2', vertexColors: true, roughness: 0.58, flatShading, side: FrontSide, clippingPlanes });
  }
  return new MeshStandardMaterial({
    color,
    roughness: mode === 'clay' ? 0.52 : 0.4,
    metalness: mode === 'clay' ? 0.08 : 0.03,
    flatShading,
    side: FrontSide,
    colorWrite: mode !== 'edges',
    depthWrite: true,
    depthTest: true,
    polygonOffset: mode === 'solid-wire' || mode === 'edges',
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    clippingPlanes,
  });
}

export function createMaterialLibrary(
  mode: DisplayMode,
  clipPlane: Plane | null,
  theme: ViewportTheme = 'dark',
  tintSolvedRegion = true,
  sourceRoleColors = true,
): MaterialLibrary {
  const clippingPlanes = clipPlane ? [clipPlane] : [];
  const surfaces = Object.fromEntries(classes.map((materialClass) => [
    materialClass,
    surfaceMaterial(mode, materialClass, clippingPlanes, theme, false, sourceRoleColors),
  ])) as Record<SurfaceMaterialClass, ReturnType<typeof surfaceMaterial>>;
  const solvedSurfaces = tintSolvedRegion
    ? Object.fromEntries(classes.map((materialClass) => [
        materialClass,
        surfaceMaterial(mode, materialClass, clippingPlanes, theme, true, sourceRoleColors),
      ])) as Record<SurfaceMaterialClass, ReturnType<typeof surfaceMaterial>>
    : surfaces;
  const wire = new MeshBasicMaterial({
    color: tokenColor('--vp-wire-material', theme === 'light' ? '#26384a' : '#9ed4f4'),
    wireframe: true, transparent: true, opacity: theme === 'light' ? 0.42 : 0.34, side: FrontSide, clippingPlanes,
  });
  // Hard-boundary edges were the last cyan in the application.
  const edge = new LineBasicMaterial({
    color: tokenColor('--vp-edge-material', theme === 'light' ? '#a5391b' : '#e0673f'),
    transparent: true, opacity: 0.96, clippingPlanes,
  });
  const stencilBack = new MeshBasicMaterial({
    colorWrite: false, depthWrite: false, depthTest: false, side: BackSide, clippingPlanes,
    stencilWrite: true, stencilFunc: AlwaysStencilFunc, stencilFail: KeepStencilOp,
    stencilZFail: KeepStencilOp, stencilZPass: IncrementWrapStencilOp,
  });
  const stencilFront = new MeshBasicMaterial({
    colorWrite: false, depthWrite: false, depthTest: false, side: FrontSide, clippingPlanes,
    stencilWrite: true, stencilFunc: AlwaysStencilFunc, stencilFail: KeepStencilOp,
    stencilZFail: KeepStencilOp, stencilZPass: DecrementWrapStencilOp,
  });
  const cap = new MeshStandardMaterial({
    color: tokenColor('--vp-cap-material', theme === 'light' ? '#a5674a' : '#c07a4e'),
    metalness: 0.02, roughness: 0.72, side: FrontSide,
    stencilWrite: true, stencilRef: 0, stencilFunc: NotEqualStencilFunc,
    stencilFail: ReplaceStencilOp, stencilZFail: ReplaceStencilOp, stencilZPass: ReplaceStencilOp,
  });
  const all = [...new Set([...Object.values(surfaces), ...Object.values(solvedSurfaces), wire, edge, stencilBack, stencilFront, cap])];
  return { surfaces, solvedSurfaces, wire, edge, stencilBack, stencilFront, cap, all };
}
