import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { restoreAutosave, writeAutosave } from '../stores/autosave';
import { NATIVE_FAMILIES, designForFamily, mergeDesignForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { PARAMETER_REGISTRY, fieldAcceptsExpression, fieldIsVisible } from './parameterRegistry';
import { buildParameterCatalog } from './parameterCatalogWire';
import { nativeCadLimitation, nativeSymmetryLimitation } from './nativeFamilies';

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
    expect(design).not.toHaveProperty('cross_section');
    expect(wire).not.toHaveProperty('cross_section');
  });

  it.each(NATIVE_FAMILIES)('omits inert legacy cross-section defaults for %s', (family) => {
    const design = designForFamily(family);
    design.cross_section = { exponent: 2, aspect_ratio: 1 };
    expect(serializeDesign(design)).not.toHaveProperty('cross_section');
    const restored = hydrateDesignDocument({ ...serializeDesign(design), cross_section: design.cross_section });
    expect(restored).not.toHaveProperty('cross_section');
    expect(serializeDesign(restored)).toEqual(serializeDesign(designForFamily(family)));
  });

  it.each(NATIVE_FAMILIES)('refuses unsupported cross-section intent for %s', (family) => {
    for (const cross_section of [{ exponent: 4, aspect_ratio: 1 }, { exponent: 2, aspect_ratio: 1.5 }]) {
      const design = { ...designForFamily(family), cross_section };
      expect(() => serializeDesign(design)).toThrow('requires cross_section exponent=2 and aspect_ratio=1');
      expect(() => hydrateDesignDocument({ formula: family, cross_section })).toThrow('requires cross_section exponent=2 and aspect_ratio=1');
    }
  });

  it.each(NATIVE_FAMILIES)('restores %s from autosave', (family) => {
    const records = new Map<string, string>();
    const storage = {
      getItem: (key: string) => records.get(key) ?? null,
      setItem: (key: string, value: string) => { records.set(key, value); },
      removeItem: (key: string) => { records.delete(key); },
    };
    const design = designForFamily(family);
    useDesignStore.getState().replaceDocument(design);
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
    legacy.cross_section = { exponent: 4, aspect_ratio: 1.5 };
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
    for (const field of catalog.parameters.filter((field) => field.id.startsWith('morph.')
      || field.id.startsWith('enclosure.') || field.id === 'simulation.sim_type')) {
      expect(field.families.some((family) => (NATIVE_FAMILIES as readonly string[]).includes(family)), field.id).toBe(false);
    }
  });
});


it('keeps the server defaults fixture fresh', () => {
  const fixture = JSON.parse(new TextDecoder().decode(readFileSync('../server/tests/fixtures/native-family-defaults.json')));
  expect(Object.fromEntries(NATIVE_FAMILIES.map((family) => [family, serializeDesign(designForFamily(family))]))).toEqual(fixture);
});

it.each([
  ['OSSE-ADAPTER', 'OSSE-ADAPTER-AXIAL'], ['OSSE-ADAPTER-AXIAL', 'OSSE-ADAPTER'],
  ['OSSE-AXIAL', 'OSSE-ARC'], ['OSSE-ARC', 'OSSE-AXIAL'],
] as const)('preserves shared native body and mesh edits from %s to %s', (from, to) => {
  const current = designForFamily(from);
  Object.assign(current, { L: 140, r0: 14, a: 42, a0: 14, k: 1.1, scale: 1.2 });
  Object.assign(current.mesh, { throat_resolution: 2, mouth_resolution: 4, rear_resolution: 3, angular_segments: 64, length_segments: 30 });
  if (current.throat_adapter) current.throat_adapter.driver_handle_mm = 11;
  const switched = mergeDesignForFamily(current, to);
  for (const key of ['L', 'r0', 'a', 'a0', 'k', 'scale'] as const) expect(switched[key]).toBe(current[key]);
  for (const key of ['throat_resolution', 'mouth_resolution', 'rear_resolution', 'angular_segments', 'length_segments'] as const) expect(switched.mesh[key]).toBe(current.mesh[key]);
  if (current.throat_adapter) expect(switched.throat_adapter).toEqual(current.throat_adapter);
  expect(switched.axial_scale).toBe(to.endsWith('AXIAL') ? 1 : undefined);
  expect(switched.terminating_arc).toEqual(to === 'OSSE-ARC' ? designForFamily(to).terminating_arc : undefined);
});

it.each(NATIVE_FAMILIES)('explains native domain and CAD limits for %s', (formula) => {
  for (const symmetry of ['half_xz', 'half_yz', 'quarter']) expect(nativeSymmetryLimitation(formula, symmetry)).toContain('Select Full');
  for (const symmetry of ['auto', 'full']) expect(nativeSymmetryLimitation(formula, symmetry)).toBeNull();
  expect(nativeCadLimitation(formula)).toContain('Export STEP or STL');
});
