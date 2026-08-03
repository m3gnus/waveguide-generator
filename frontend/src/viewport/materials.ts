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
import type { DisplayMode, MaterialLibrary, SurfaceMaterialClass } from './types';

const classes: SurfaceMaterialClass[] = [
  'horn-smooth', 'horn-flat', 'boundary-smooth', 'boundary-flat', 'enclosure-smooth', 'enclosure-flat',
];

function classColor(materialClass: SurfaceMaterialClass): Color {
  if (materialClass.startsWith('enclosure')) return new Color('#4c5964');
  if (materialClass.startsWith('boundary')) return new Color('#c18b5d');
  return new Color('#bdc7ce');
}

function zebraMaterial(clippingPlanes: Plane[]): ShaderMaterial {
  return new ShaderMaterial({
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
        vec3 incident = normalize(vWorldPosition - cameraPosition);
        vec3 reflected = reflect(incident, normalize(vWorldNormal));
        float band = smoothstep(0.38, 0.62, 0.5 + 0.5 * sin(reflected.y * 42.0));
        vec3 darkBand = vec3(0.025, 0.035, 0.045);
        vec3 lightBand = vec3(0.88, 0.96, 1.0);
        gl_FragColor = vec4(mix(darkBand, lightBand, band), 1.0);
      }
    `,
  });
}

function surfaceMaterial(mode: DisplayMode, materialClass: SurfaceMaterialClass, clippingPlanes: Plane[]) {
  const color = classColor(materialClass);
  if (mode === 'wireframe') {
    return new MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.9, clippingPlanes });
  }
  if (mode === 'xray') {
    return new MeshStandardMaterial({
      color, roughness: 0.32, metalness: 0.06, transparent: true, opacity: 0.22,
      depthWrite: false, side: DoubleSide, clippingPlanes,
    });
  }
  if (mode === 'zebra') return zebraMaterial(clippingPlanes);
  if (mode === 'curvature') {
    return new MeshStandardMaterial({ color: '#ffffff', vertexColors: true, roughness: 0.6, side: DoubleSide, clippingPlanes });
  }
  return new MeshStandardMaterial({
    color,
    roughness: mode === 'clay' ? 0.52 : 0.4,
    metalness: mode === 'clay' ? 0.08 : 0.03,
    side: FrontSide,
    polygonOffset: mode === 'solid-wire',
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    clippingPlanes,
  });
}

export function createMaterialLibrary(mode: DisplayMode, clipPlane: Plane | null): MaterialLibrary {
  const clippingPlanes = clipPlane ? [clipPlane] : [];
  const zebra = mode === 'zebra' ? zebraMaterial(clippingPlanes) : null;
  const surfaces = Object.fromEntries(classes.map((materialClass) => [
    materialClass,
    zebra ?? surfaceMaterial(mode, materialClass, clippingPlanes),
  ])) as Record<SurfaceMaterialClass, ReturnType<typeof surfaceMaterial>>;
  const wire = new MeshBasicMaterial({ color: '#26343d', wireframe: true, transparent: true, opacity: 0.55, clippingPlanes });
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
    color: '#d18a56', metalness: 0.02, roughness: 0.72, side: DoubleSide,
    stencilWrite: true, stencilRef: 0, stencilFunc: NotEqualStencilFunc,
    stencilFail: ReplaceStencilOp, stencilZFail: ReplaceStencilOp, stencilZPass: ReplaceStencilOp,
  });
  const all = [...new Set([...Object.values(surfaces), wire, edge, stencilBack, stencilFront, cap])];
  return { surfaces, wire, edge, stencilBack, stencilFront, cap, all };
}
