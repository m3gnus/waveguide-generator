/**
 * Shader-side counterpart to fieldPlaneColor.ts.
 *
 * The solver uses the e^-iwt convention, so instantaneous pressure is
 * re*cos(wt) + im*sin(wt). A +z phasor exp(+ikz) therefore becomes
 * cos(kz-wt), whose phase fronts move toward +z as time advances.
 */
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
  uniform sampler2D uFieldComplex;
  uniform sampler2D uColorLut;
  uniform sampler2D uMask;
  uniform float uDisplayMode;
  uniform float uFieldMaxDb;
  uniform float uWindowMin;
  uniform float uWindowMax;
  uniform float uTimePhase;
  uniform float uIsolines;
  uniform float uOpacity;
  varying vec2 vFieldUv;

  float pressureSpl(float magnitude) {
    return 20.0 * log(max(magnitude, 2e-5 * 1e-12) / 2e-5) / log(10.0);
  }

  void main() {
    #include <clipping_planes_fragment>
    // Coverage mask: 0 shows the field, 1 hides it (inside the model or on
    // its surface band). The worker writes a linear ramp across the band
    // edge and the texture samples with linear filtering, so a smoothstep
    // here yields an anti-aliased silhouette instead of a texel staircase.
    float maskCoverage = texture2D(uMask, vFieldUv).r;
    if (maskCoverage >= 0.995) discard;
    float maskAlpha = 1.0 - smoothstep(0.0, 1.0, maskCoverage);
    vec2 field = texture2D(uFieldComplex, vFieldUv).rg;
    float spl = pressureSpl(length(field));
    float value;
    if (uDisplayMode < 0.5) {
      value = spl;
    } else if (uDisplayMode < 1.5) {
      value = spl - uFieldMaxDb;
    } else if (uDisplayMode < 2.5) {
      value = atan(field.y, field.x) * 180.0 / 3.141592653589793;
    } else {
      value = field.x * cos(uTimePhase) + field.y * sin(uTimePhase);
    }

    float normalized = clamp(
      (value - uWindowMin) / max(uWindowMax - uWindowMin, 1e-30),
      0.0,
      1.0
    );
    vec3 color = texture2D(uColorLut, vec2(normalized, 0.5)).rgb;

    if (uIsolines > 0.5 && uDisplayMode < 1.5) {
      float isoline = value / 6.0;
      float lineWidth = max(fwidth(isoline), 1e-6);
      float distanceToLine = abs(fract(isoline + 0.5) - 0.5);
      float line = 1.0 - smoothstep(0.0, lineWidth, distanceToLine);
      color = mix(color, color * 0.58, line * 0.32);
    }

    gl_FragColor = vec4(color, uOpacity * maskAlpha);
  }
`;
