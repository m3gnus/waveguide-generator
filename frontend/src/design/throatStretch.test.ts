import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, mergeDesignForFamily, serializeDesign } from '../stores/design';
import { buildParameterCatalog } from './parameterCatalogWire';
import { PARAMETER_REGISTRY, fieldAcceptsExpression, fieldIsVisible } from './parameterRegistry';

for (const family of ['OSSE', 'R-OSSE'] as const) {
  describe(`${family} throat stretch`, () => {
    it('round-trips plain values and preserves absent old-design fields', () => {
      const design = designForFamily(family);
      design.s1 = .45; design.s2 = .2;
      const wire = serializeDesign(design);
      const loaded = hydrateDesignDocument(wire);
      expect(serializeDesign(loaded)).toEqual(wire);
      delete wire.s1; delete wire.s2;
      const old = serializeDesign(hydrateDesignDocument(wire));
      expect(old.s1).toBeNull(); expect(old.s2).toBeNull();
      expect(hydrateDesignDocument(wire).s1).toBe(0);
    });
    it('carries stretch between OSSE families and drops it on another formula', () => {
      const design = { ...designForFamily(family), s1: .45, s2: .2 };
      const next = mergeDesignForFamily(design, family === 'OSSE' ? 'R-OSSE' : 'OSSE');
      expect(next.s1).toBe(.45); expect(next.s2).toBe(.2);
      expect(serializeDesign(mergeDesignForFamily(next, 'ICW'))).not.toHaveProperty('s1');
      expect(serializeDesign(mergeDesignForFamily(next, 'FREEFORM'))).not.toHaveProperty('s2');
    });
    it('publishes bounds and plain-number help beside profile parameters', () => {
      for (const key of ['s1', 's2']) {
        const field = PARAMETER_REGISTRY.find((p) => p.id === `common.${key}`)!;
        expect(field.section).toBe('Profile Dimensions');
        expect(fieldIsVisible(field, designForFamily(family))).toBe(true);
        expect(fieldIsVisible(field, designForFamily('ICW'))).toBe(false);
        expect(fieldIsVisible(field, designForFamily('FREEFORM'))).toBe(false);
        expect(fieldAcceptsExpression(field)).toBe(false);
        expect(field.description).toContain('plain number from 0 to 10');
        const catalog = buildParameterCatalog().parameters.find((p) => p.id === field.id)!;
        expect(catalog.default_by_family).toEqual({ OSSE: 0, 'R-OSSE': 0 });
        expect(catalog.editor_bounds).toEqual({ minimum: 0, maximum: 10 });
      }
    });
  });
}
