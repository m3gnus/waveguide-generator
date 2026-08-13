import { DoubleSide, FrontSide, MeshStandardMaterial, ShaderMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { createMaterialLibrary } from './materials';
import type { DisplayMode } from './types';

const surfaceModes: DisplayMode[] = ['clay', 'solid-wire', 'wireframe', 'xray', 'zebra', 'curvature', 'edges'];

describe('viewport material mode matrix', () => {
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
});
