import { DoubleSide, FrontSide, MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'vitest';
import { createMaterialLibrary } from './materials';
import type { DisplayMode } from './types';

const surfaceModes: DisplayMode[] = ['clay', 'solid-wire', 'wireframe', 'xray', 'zebra', 'curvature', 'edges'];

describe('viewport material mode matrix', () => {
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
});
