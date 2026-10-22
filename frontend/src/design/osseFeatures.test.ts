import { afterEach, describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, NATIVE_FAMILIES, resetDesignStore, serializeDesign, useDesignStore, type DesignFamily } from '../stores/design';
import { osseFeatures, osseFormula } from './osseFeatures';

afterEach(resetDesignStore);

const variants = [
  ['OSSE', 'none', 'plain', 'off'],
  ['OSSE-ADAPTER', 'authored', 'plain', 'off'],
  ['OSSE-ADAPTER-CONTROLS', 'controls', 'plain', 'off'],
  ['OSSE-ADAPTER-AXIAL', 'authored', 'plain', 'on'],
  ['OSSE-ROUNDOVER', 'none', 'roundover', 'off'],
  ['OSSE-AXIAL', 'none', 'plain', 'on'],
  ['OSSE-ARC', 'none', 'arc', 'off'],
] as const;

describe('OSSE feature choices', () => {
  it.each(variants)('maps %s in both directions', (formula, throat, mouth, axial) => {
    expect(osseFeatures(formula)).toEqual({ throat, mouth, axial });
    expect(osseFormula({ throat, mouth, axial })).toBe(formula);
  });

  it('rejects all unsupported combinations and excludes unrelated families', () => {
    let supported = 0;
    for (const throat of ['none', 'authored', 'controls'] as const) {
      for (const mouth of ['plain', 'roundover', 'arc'] as const) {
        for (const axial of ['off', 'on'] as const) {
          const allowed = Number(throat !== 'none') + Number(mouth !== 'plain') + Number(axial === 'on') <= 1
            || (throat === 'authored' && mouth === 'plain');
          expect(Boolean(osseFormula({ throat, mouth, axial }))).toBe(allowed);
          supported += Number(allowed);
        }
      }
    }
    expect(supported).toBe(7);
    for (const family of ['SOURCE-DISK', 'LOOKUP', 'R-OSSE', 'ICW', 'FREEFORM']) expect(osseFeatures(family)).toBeUndefined();
  });

  it.each(NATIVE_FAMILIES)('loads and displays %s without changing serialized build inputs', (family) => {
    const original = designForFamily(family);
    if (family !== 'SOURCE-DISK') Object.assign(original, { L: 141.123456789, r0: 13.5, a: 39, a0: 12, k: 1.5, scale: 1.25 });
    if (original.axial_scale !== undefined) original.axial_scale = 1.75;
    if (original.terminating_arc) original.terminating_arc.join_t = .123456789;
    if (original.throat_adapter?.mode === 'authored') original.throat_adapter.driver_handle_mm = 11.123456789;
    if (family === 'OSSE-ROUNDOVER') original.mesh.mouth_roundover_radius_mm = 32.123456789;
    const wire = serializeDesign(original);
    useDesignStore.getState().replaceDocument(hydrateDesignDocument(wire));
    const current = useDesignStore.getState().design;
    expect(current.formula).toBe(family);
    expect(osseFeatures(current.formula)).toEqual(family === 'SOURCE-DISK' ? undefined : expect.any(Object));
    expect(JSON.stringify(serializeDesign(current))).toBe(JSON.stringify(wire));
  });

  it.each(variants.slice(1))('carries shared body edits across %s and plain OSSE, retaining complete undo', (family) => {
    const original = { ...designForFamily('OSSE'), L: 141, r0: 13.5, a: 39, a0: 12, k: 1.5, scale: 1.25, h: 3, s: .6 };
    original._expressions = { L: { value: 141, raw: '140 + 1', dialect: 'ath' as const } };
    original.morph.target_shape = 2;
    original.enclosure.depth = 250;
    useDesignStore.getState().replaceDocument(original);
    useDesignStore.getState().setFamily(family as DesignFamily, { preserveOsseBody: true });
    const variant = useDesignStore.getState().design;
    expect(variant).toMatchObject({ formula: family, L: 141, r0: 13.5, a: 39, a0: 12, k: 1.5, scale: 1.25, s: 0 });
    expect(variant._expressions).toBeUndefined();
    if (variant.axial_scale !== undefined) expect(variant.axial_scale).toBe(1.25);
    useDesignStore.getState().setFamily('OSSE', { preserveOsseBody: true });
    expect(useDesignStore.getState().design).toMatchObject({ formula: 'OSSE', L: 141, r0: 13.5, a: 39, a0: 12, k: 1.5, scale: 1.25, s: 0 });
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design).toEqual(variant);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design).toEqual(original);
    useDesignStore.getState().redo();
    expect(useDesignStore.getState().design).toEqual(variant);
  });
});
