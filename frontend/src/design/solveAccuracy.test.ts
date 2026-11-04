import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { NATIVE_FAMILIES, designForFamily, mergeDesignForFamily, serializeDesign, type DesignFamily } from '../stores/design';

describe('solve surface deviation', () => {
  it.each(['OSSE', 'R-OSSE', 'ICW', 'FREEFORM', ...NATIVE_FAMILIES] as const)('starts a new %s design Off', (family) => {
    const fresh = designForFamily(family);
    expect(fresh.mesh.surface_deviation_mm ?? null).toBeNull();
    expect(serializeDesign(fresh).mesh).not.toHaveProperty('surface_deviation_mm');
    const wire = serializeDesign(fresh);
    const mesh = wire.mesh as Record<string, unknown>;
    delete mesh.surface_deviation_mm;
    const loaded = hydrateDesignDocument(wire);
    expect(loaded.mesh.surface_deviation_mm).toBeUndefined();
    expect(serializeDesign(loaded).mesh).not.toHaveProperty('surface_deviation_mm');
  });

  it('retains an explicit setting and restores Off without adding a default on reopen', () => {
    const fresh = designForFamily('OSSE');
    fresh.mesh.surface_deviation_mm = 0.2;
    expect(hydrateDesignDocument(serializeDesign(fresh)).mesh.surface_deviation_mm).toBe(0.2);
    fresh.mesh.surface_deviation_mm = null;
    expect(hydrateDesignDocument(serializeDesign(fresh)).mesh.surface_deviation_mm).toBeUndefined();
  });
});

const families: DesignFamily[] = ['OSSE', 'R-OSSE', 'ICW', 'FREEFORM', ...NATIVE_FAMILIES];
it.each(families)('preserves accuracy intent when switching from %s', (from) => {
  for (const to of families) {
    for (const value of [undefined, null, 0.2]) {
      const current = designForFamily(from);
      if (value === undefined) delete current.mesh.surface_deviation_mm;
      else current.mesh.surface_deviation_mm = value;
      const next = mergeDesignForFamily(current, to);
      expect(next.mesh.surface_deviation_mm).toBe(to === 'SOURCE-DISK' ? undefined : value);
    }
  }
});
