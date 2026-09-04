import { BackSide, Color, DoubleSide, FrontSide, MeshStandardMaterial, Plane, ShaderMaterial, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { createMaterialLibrary } from './materials';
import type { DisplayMode } from './types';

const surfaceModes: DisplayMode[] = ['clay', 'solid-wire', 'wireframe', 'xray', 'zebra', 'curvature', 'edges'];

/** Rec. 709 relative luminance, enough to say which of two clays is darker. */
const luminance = (color: Color) => 0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b;

describe('viewport material mode matrix', () => {
  it('shares the mutable clip-plane reference and compiles unclipped materials separately', () => {
    const plane = new Plane(new Vector3(1, 0, 0), 0);
    const clipped = createMaterialLibrary('clay', plane);
    for (const material of [
      ...Object.values(clipped.surfaces),
      clipped.wire,
      clipped.edge,
      clipped.stencilBack,
      clipped.stencilFront,
    ]) expect(material.clippingPlanes?.[0]).toBe(plane);
    clipped.all.forEach((material) => material.dispose());

    const unclipped = createMaterialLibrary('clay', null);
    for (const material of Object.values(unclipped.surfaces)) {
      expect(material.clippingPlanes).toEqual([]);
    }
    unclipped.all.forEach((material) => material.dispose());
  });

  it('draws both sides in normals mode so an inverted patch is visible, not invisible', () => {
    const library = createMaterialLibrary('normals', null);
    for (const material of Object.values(library.surfaces)) {
      expect(material.side).toBe(DoubleSide);
      expect(material).toBeInstanceOf(ShaderMaterial);
      // Back faces must be flagged, not shaded like front faces.
      expect((material as ShaderMaterial).fragmentShader).toContain('gl_FrontFacing');
    }
    library.all.forEach((material) => material.dispose());
  });

  it.each(surfaceModes)('%s uses the orientation-contract side', (mode) => {
    const library = createMaterialLibrary(mode, null);
    const expected = mode === 'xray' ? DoubleSide : FrontSide;
    for (const material of Object.values(library.surfaces)) expect(material.side).toBe(expected);
    expect(library.wire.side).toBe(FrontSide);
    expect(library.cap.side).toBe(FrontSide);
    library.all.forEach((material) => material.dispose());
  });

  it('keeps the horn accent-tinted and separates the enclosure in both themes', () => {
    for (const theme of ['dark', 'light'] as const) {
      const library = createMaterialLibrary('clay', null, theme);
      const horn = library.surfaces['horn-smooth'] as MeshStandardMaterial;
      const enclosure = library.surfaces['enclosure-smooth'] as MeshStandardMaterial;
      expect(horn.color.getHexString()).not.toBe('ffffff');
      expect(enclosure.color.getHexString()).not.toBe(horn.color.getHexString());
      library.all.forEach((material) => material.dispose());
    }
  });

  it('can tint or match the solved symmetry region from viewer preferences', () => {
    const tinted = createMaterialLibrary('clay', null, 'dark', true);
    expect((tinted.solvedSurfaces['horn-smooth'] as MeshStandardMaterial).color.getHexString())
      .not.toBe((tinted.surfaces['horn-smooth'] as MeshStandardMaterial).color.getHexString());
    tinted.all.forEach((material) => material.dispose());

    const matched = createMaterialLibrary('clay', null, 'dark', false);
    expect(matched.solvedSurfaces['horn-smooth']).toBe(matched.surfaces['horn-smooth']);
    matched.all.forEach((material) => material.dispose());
  });

  it('uses true flat shading for flat classes, including a face-normal zebra shader', () => {
    const clay = createMaterialLibrary('clay', null);
    expect((clay.surfaces['horn-smooth'] as MeshStandardMaterial).flatShading).toBe(false);
    expect((clay.surfaces['horn-flat'] as MeshStandardMaterial).flatShading).toBe(true);
    clay.all.forEach((material) => material.dispose());

    const zebra = createMaterialLibrary('zebra', null);
    expect(zebra.surfaces['horn-flat']).not.toBe(zebra.surfaces['horn-smooth']);
    expect((zebra.surfaces['horn-flat'] as ShaderMaterial).defines.FLAT_SHADED).toBe(1);
    expect((zebra.surfaces['horn-smooth'] as ShaderMaterial).defines.FLAT_SHADED).toBeUndefined();
    zebra.all.forEach((material) => material.dispose());
  });
  it('paints each source role in its Fusion appearance colour', () => {
    const library = createMaterialLibrary('clay', null, 'dark');
    const hex = (materialClass: 'hf-smooth' | 'mf-smooth' | 'lf-smooth' | 'port-smooth') =>
      (library.surfaces[materialClass] as MeshStandardMaterial).color.getHexString();
    // The four values WGLink paints on a face in Fusion, unchanged. A drift
    // here means the viewport and the CAD document disagree about which
    // driver the user is looking at.
    expect(hex('hf-smooth')).toBe('ff0000');
    expect(hex('mf-smooth')).toBe('ffbb00');
    expect(hex('lf-smooth')).toBe('004cff');
    expect(hex('port-smooth')).toBe('449648');
    library.all.forEach((material) => material.dispose());
  });

  it('leaves a role colour untinted by the solved-region wash', () => {
    const library = createMaterialLibrary('clay', null, 'dark', true);
    expect((library.solvedSurfaces['hf-smooth'] as MeshStandardMaterial).color.getHexString())
      .toBe((library.surfaces['hf-smooth'] as MeshStandardMaterial).color.getHexString());
    library.all.forEach((material) => material.dispose());
  });

  it('paints the exposed interior of a cut in a back-facing material that clips with the model', () => {
    const plane = new Plane(new Vector3(1, 0, 0), 0);
    for (const theme of ['dark', 'light'] as const) {
      const library = createMaterialLibrary('clay', plane, theme);
      const interior = library.interior as MeshStandardMaterial;
      expect(interior.side).toBe(BackSide);
      // Without this the interior paints straight through the clipped-away half.
      expect(interior.clippingPlanes?.[0]).toBe(plane);
      expect(library.all).toContain(interior);
      // Darker than the shell it is the inside of, in both themes -- an
      // interior lighter than its own exterior does not read as an interior.
      const shell = library.surfaces['horn-smooth'] as MeshStandardMaterial;
      expect(luminance(interior.color)).toBeLessThan(luminance(shell.color));
      library.all.forEach((material) => material.dispose());
    }
  });

  it('leaves back faces alone in the modes that already own them', () => {
    // xray is double-sided already, normals flags its back faces as a fault,
    // wireframe paints no surface, and edges writes no colour from this pass.
    for (const mode of ['xray', 'normals', 'wireframe', 'edges'] as DisplayMode[]) {
      const library = createMaterialLibrary(mode, null);
      expect(library.interior).toBeNull();
      library.all.forEach((material) => material.dispose());
    }
    for (const mode of ['clay', 'solid-wire', 'curvature', 'zebra'] as DisplayMode[]) {
      const library = createMaterialLibrary(mode, null);
      expect(library.interior).not.toBeNull();
      library.all.forEach((material) => material.dispose());
    }
  });

  it('falls back to the neutral cap material when role colouring is off', () => {
    const off = createMaterialLibrary('clay', null, 'dark', true, false);
    const neutral = (off.surfaces['source-smooth'] as MeshStandardMaterial).color.getHexString();
    for (const materialClass of ['hf-smooth', 'mf-smooth', 'lf-smooth', 'port-smooth'] as const) {
      expect((off.surfaces[materialClass] as MeshStandardMaterial).color.getHexString()).toBe(neutral);
    }
    off.all.forEach((material) => material.dispose());
  });
});
