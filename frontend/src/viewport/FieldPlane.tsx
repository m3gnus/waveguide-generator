import { useEffect, useMemo } from 'react';
import {
  ClampToEdgeWrapping,
  DataTexture,
  DoubleSide,
  FloatType,
  LinearFilter,
  Matrix4,
  RedFormat,
  RGBAFormat,
  ShaderMaterial,
  UnsignedByteType,
  Vector3,
  type Plane,
} from 'three';
import type { DecodedFieldPlane, FieldPlaneSpec } from '../api/fieldPlane';
import { buildLutRgba, FIELD_PLANE_WINDOW_DB, maxFieldSplDb } from './fieldPlaneColor';
import { FIELD_PLANE_FRAGMENT_SHADER, FIELD_PLANE_VERTEX_SHADER } from './fieldPlaneShader';
import { useFieldPlaneStore } from './fieldPlaneStore';
import type { DemandRenderScheduler } from './demandRender';

interface FieldPlaneProps {
  unitsPerMetre: number;
  clipPlane: Plane | null;
  colormap: readonly string[];
  scheduler: DemandRenderScheduler;
}

interface ReadyFieldPlaneProps extends FieldPlaneProps {
  plane: FieldPlaneSpec;
  field: DecodedFieldPlane;
}

function floatTexture(data: Float32Array, width: number, height: number): DataTexture {
  const texture = new DataTexture(data, width, height, RedFormat, FloatType);
  texture.internalFormat = 'R32F';
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.wrapS = ClampToEdgeWrapping;
  texture.wrapT = ClampToEdgeWrapping;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

function lutTexture(colormap: readonly string[]): DataTexture {
  const data = buildLutRgba(colormap);
  const texture = new DataTexture(data, data.length / 4, 1, RGBAFormat, UnsignedByteType);
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.wrapS = ClampToEdgeWrapping;
  texture.wrapT = ClampToEdgeWrapping;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

export function fieldPlaneTransform(plane: FieldPlaneSpec, unitsPerMetre: number): Matrix4 {
  const axisU = new Vector3(...plane.axis_u);
  const axisV = new Vector3(...plane.axis_v);
  const normal = axisU.clone().cross(axisV);
  const transform = new Matrix4().makeBasis(axisU, axisV, normal);
  transform.setPosition(new Vector3(...plane.origin_m).multiplyScalar(unitsPerMetre));
  return transform;
}

function ReadyFieldPlane({ plane, field, unitsPerMetre, clipPlane, colormap, scheduler }: ReadyFieldPlaneProps) {
  const textures = useMemo(() => ({
    real: floatTexture(field.real, field.header.nx, field.header.ny),
    imag: floatTexture(field.imag, field.header.nx, field.header.ny),
    lut: lutTexture(colormap),
  }), [colormap, field]);
  const maxDb = useMemo(() => maxFieldSplDb(field.real, field.imag), [field]);
  const material = useMemo(() => new ShaderMaterial({
    clipping: clipPlane !== null,
    clippingPlanes: clipPlane ? [clipPlane] : [],
    depthWrite: false,
    depthTest: true,
    side: DoubleSide,
    transparent: true,
    toneMapped: false,
    uniforms: {
      uFieldReal: { value: textures.real },
      uFieldImag: { value: textures.imag },
      uColorLut: { value: textures.lut },
      uWindowMinDb: { value: maxDb - FIELD_PLANE_WINDOW_DB },
      uWindowMaxDb: { value: maxDb },
      uOpacity: { value: 0.92 },
    },
    vertexShader: FIELD_PLANE_VERTEX_SHADER,
    fragmentShader: FIELD_PLANE_FRAGMENT_SHADER,
  }), [clipPlane, maxDb, textures]);
  const transform = useMemo(() => fieldPlaneTransform(plane, unitsPerMetre), [plane, unitsPerMetre]);

  useEffect(() => {
    scheduler.schedule();
  }, [material, scheduler, transform]);
  useEffect(() => () => {
    textures.real.dispose();
    textures.imag.dispose();
    textures.lut.dispose();
  }, [textures]);
  useEffect(() => () => material.dispose(), [material]);

  return <mesh matrix={transform} matrixAutoUpdate={false} material={material} renderOrder={1_000}>
    <planeGeometry args={[plane.width_m * unitsPerMetre, plane.height_m * unitsPerMetre]}/>
  </mesh>;
}

export function FieldPlane(props: FieldPlaneProps) {
  const enabled = useFieldPlaneStore((state) => state.enabled);
  const status = useFieldPlaneStore((state) => state.status);
  const plane = useFieldPlaneStore((state) => state.plane);
  const field = useFieldPlaneStore((state) => state.field);
  if (!enabled || status !== 'ready' || !plane || !field) return null;
  return <ReadyFieldPlane {...props} plane={plane} field={field}/>;
}
