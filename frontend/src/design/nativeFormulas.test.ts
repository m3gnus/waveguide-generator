import { afterEach, describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { restoreAutosave, writeAutosave } from '../stores/autosave';
import { NATIVE_FAMILIES, designForFamily, mergeDesignForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { PARAMETER_REGISTRY, fieldAcceptsExpression, fieldIsVisible } from './parameterRegistry';
import { buildParameterCatalog } from './parameterCatalogWire';

afterEach(resetDesignStore);

describe('native formula editing and persistence', () => {
  it.each(NATIVE_FAMILIES)('hydrates and serializes every %s control without loss', (family) => {
    const design = designForFamily(family);
    const wire = serializeDesign(design);
    expect(serializeDesign(hydrateDesignDocument(wire))).toEqual(wire);
    const fields = PARAMETER_REGISTRY.filter((field) => field.id.startsWith('native.') && fieldIsVisible(field, design));
    expect(fields.length).toBeGreaterThan(0);
    for (const field of fields) {
      expect(field.description).toBeTruthy();
      expect(fieldAcceptsExpression(field)).toBe(false);
    }
    expect(PARAMETER_REGISTRY.filter((field) => !field.id.startsWith('native.') && fieldIsVisible(field, design))
      .every((field) => field.id.startsWith('simulation.') || field.kind === 'indicator')).toBe(true);
    expect(wire).not.toHaveProperty('guiding_curve');
    expect(wire).not.toHaveProperty('text_import_version');
  });

  it.each(NATIVE_FAMILIES)('restores %s from autosave', (family) => {
    const records = new Map<string, string>();
    const storage = {
      getItem: (key: string) => records.get(key) ?? null,
      setItem: (key: string, value: string) => { records.set(key, value); },
      removeItem: (key: string) => { records.delete(key); },
    };
    const design = designForFamily(family);
    useDesignStore.getState().loadDesign(design);
    expect(writeAutosave(storage)).toBe(true);
    resetDesignStore();
    expect(restoreAutosave(storage)).toBe(true);
    expect(useDesignStore.getState().design).toEqual(design);
  });

  it('edits each independent literal cubic pole and preserves precision on the wire', () => {
    useDesignStore.getState().setFamily('OSSE-ADAPTER-CONTROLS');
    const poles = [[-31.712345678901, 16.234567890123], [-22.134567890123, 16.956789012345], [-9.312345678901, 20.456789012345]];
    poles.forEach((pair, pole) => pair.forEach((value, axis) => {
      useDesignStore.getState().updateField(`throat_adapter.control_points_mm.${pole}.${axis}`, value);
    }));
    expect(serializeDesign(useDesignStore.getState().design).throat_adapter).toMatchObject({ control_points_mm: poles });
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.throat_adapter?.control_points_mm?.[2][1]).toBe(20.4);
  });

  it('switches families without retaining incompatible active geometry', () => {
    const legacy = designForFamily('OSSE');
    legacy.morph.target_shape = 2;
    legacy.enclosure.depth = 250;
    legacy.throat_ext_length = 10;
    legacy.text_import_version = 'ath-2026-08c-v1';
    for (const family of NATIVE_FAMILIES) {
      const native = mergeDesignForFamily(legacy, family);
      expect(native).toEqual(designForFamily(family));
      const switched = mergeDesignForFamily(native, 'OSSE');
      expect(serializeDesign(switched)).not.toHaveProperty('throat_adapter');
      expect(serializeDesign(switched)).not.toHaveProperty('axial_scale');
      expect(switched.mesh.mouth_roundover_radius_mm).toBeUndefined();
      expect(switched.mesh.size_mm).toBeUndefined();
    }
  });

  it('publishes native controls and their family defaults in the catalog', () => {
    const catalog = buildParameterCatalog();
    expect(catalog.design_families).toEqual(expect.arrayContaining([...NATIVE_FAMILIES]));
    const pole = catalog.parameters.find((field) => field.id === 'native.controls.2.1');
    expect(pole).toMatchObject({ families: ['OSSE-ADAPTER-CONTROLS'], unit: 'mm', accepts_expression: false,
      default_by_family: { 'OSSE-ADAPTER-CONTROLS': 20.4 } });
    const axial = catalog.parameters.find((field) => field.id === 'native.axial_scale');
    expect(axial?.default_by_family).toEqual({ 'OSSE-AXIAL': 1, 'OSSE-ADAPTER-AXIAL': 1 });
  });
});
