import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, serializeDesign } from '../stores/design';

describe('solve surface deviation', () => {
  it.each(['OSSE', 'R-OSSE', 'ICW', 'FREEFORM', 'LOOKUP'] as const)('writes the default in a new %s design only', (family) => {
    const fresh = designForFamily(family);
    expect(fresh.mesh.surface_deviation_mm).toBe(0.3);
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
