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
import type { DisplayMode, MaterialLibrary, SurfaceMaterialClass, ViewportTheme } from './types';

const classes: SurfaceMaterialClass[] = [
  'horn-smooth', 'horn-flat', 'boundary-smooth', 'boundary-flat', 'enclosure-smooth', 'enclosure-flat',
];

const fallbackColors: Record<ViewportTheme, Record<'horn' | 'boundary' | 'enclosure', string>> = {
  dark: { horn: '#8eafc6', boundary: '#a7bdc9', enclosure: '#687987' },
  light: { horn: '#9fb5c5', boundary: '#a5947d', enclosure: '#817b70' },
};

function tokenColor(token: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim() || fallback;
}

function classColor(materialClass: SurfaceMaterialClass, theme: ViewportTheme): Color {
  const family = materialClass.startsWith('enclosure')
    ? 'enclosure'
    : materialClass.startsWith('boundary') ? 'boundary' : 'horn';
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
        vec3 darkBand = vec3(0.025, 0.035, 0.045);
        vec3 lightBand = vec3(0.88, 0.96, 1.0);
        gl_FragColor = vec4(mix(darkBand, lightBand, band), 1.0);
      }
    `,
  });
}

function surfaceMaterial(mode: DisplayMode, materialClass: SurfaceMaterialClass, clippingPlanes: Plane[], theme: ViewportTheme) {
  const color = classColor(materialClass, theme);
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
  if (mode === 'curvature') {
    return new MeshStandardMaterial({ color: '#dce9ef', vertexColors: true, roughness: 0.58, flatShading, side: FrontSide, clippingPlanes });
  }
  return new MeshStandardMaterial({
    color,
    roughness: mode === 'clay' ? 0.52 : 0.4,
    metalness: mode === 'clay' ? 0.08 : 0.03,
    flatShading,
    side: FrontSide,
    polygonOffset: mode === 'solid-wire',
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    clippingPlanes,
  });
}

export function createMaterialLibrary(mode: DisplayMode, clipPlane: Plane | null, theme: ViewportTheme = 'dark'): MaterialLibrary {
  const clippingPlanes = clipPlane ? [clipPlane] : [];
  const surfaces = Object.fromEntries(classes.map((materialClass) => [
    materialClass,
    surfaceMaterial(mode, materialClass, clippingPlanes, theme),
  ])) as Record<SurfaceMaterialClass, ReturnType<typeof surfaceMaterial>>;
  const wire = new MeshBasicMaterial({
    color: tokenColor('--vp-wire-material', theme === 'light' ? '#26384a' : '#9ed4f4'),
    wireframe: true, transparent: true, opacity: theme === 'light' ? 0.42 : 0.34, side: FrontSide, clippingPlanes,
  });
  const edge = new LineBasicMaterial({ color: '#8fe4ff', transparent: true, opacity: 0.96, clippingPlanes });
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
    color: '#d18a56', metalness: 0.02, roughness: 0.72, side: FrontSide,
    stencilWrite: true, stencilRef: 0, stencilFunc: NotEqualStencilFunc,
    stencilFail: ReplaceStencilOp, stencilZFail: ReplaceStencilOp, stencilZPass: ReplaceStencilOp,
  });
  const all = [...new Set([...Object.values(surfaces), wire, edge, stencilBack, stencilFront, cap])];
  return { surfaces, wire, edge, stencilBack, stencilFront, cap, all };
}
