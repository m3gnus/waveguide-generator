/** Shader-side counterpart to fieldPlaneColor.ts. Tests assert the reference
 * pressure and window uniforms remain literal here so the two paths cannot
 * silently drift apart. */
export const FIELD_PLANE_VERTEX_SHADER = `
  #include <clipping_planes_pars_vertex>
  varying vec2 vFieldUv;
  void main() {
    vFieldUv = uv;
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vec4 mvPosition = viewMatrix * worldPosition;
    gl_Position = projectionMatrix * mvPosition;
    #include <clipping_planes_vertex>
  }
`;

export const FIELD_PLANE_FRAGMENT_SHADER = `
  #include <clipping_planes_pars_fragment>
  uniform sampler2D uFieldReal;
  uniform sampler2D uFieldImag;
  uniform sampler2D uColorLut;
  uniform float uWindowMinDb;
  uniform float uWindowMaxDb;
  uniform float uOpacity;
  varying vec2 vFieldUv;
  void main() {
    #include <clipping_planes_fragment>
    float fieldReal = texture2D(uFieldReal, vFieldUv).r;
    float fieldImag = texture2D(uFieldImag, vFieldUv).r;
    float magnitude = length(vec2(fieldReal, fieldImag));
    float spl = 20.0 * log(max(magnitude, 2e-5 * 1e-12) / 2e-5) / log(10.0);
    float normalized = clamp(
      (spl - uWindowMinDb) / max(uWindowMaxDb - uWindowMinDb, 1e-6),
      0.0,
      1.0
    );
    vec3 color = texture2D(uColorLut, vec2(normalized, 0.5)).rgb;
    gl_FragColor = vec4(color, uOpacity);
  }
`;
