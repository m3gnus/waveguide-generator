import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, mergeDesignForFamily, serializeDesign } from '../stores/design';
import { restoreAutosave, writeAutosave } from '../stores/autosave';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { designFilename } from '../stores/designName';
import { lookupRadiusWarning, parseLookupPaste } from './FreeformEditors';
import { PARAMETER_REGISTRY, fieldIsVisible, fieldMatchesQuery } from './parameterRegistry';
import { buildParameterCatalog } from './parameterCatalogWire';

it('switches to a conical whole meridian without forbidden or imported controls', () => {
  const current = designForFamily('OSSE');
  current.throat_ext_length = 20; current.slot_length = 10;
  current.text_import_version = 'ath-2026-09a-v2';
  current._expressions = { throat_ext_length: { value: 20, raw: '20' } };
  current.morph.target_shape = 2; current.enclosure.depth = 100;
  const lookup = mergeDesignForFamily(current, 'LOOKUP');
  expect(lookup.lookup_profile).toEqual([[0, 12.7], [120, 140]]);
  const wire = serializeDesign(lookup);
  for (const key of ['L', 'R', 's', 'k', 'q', 'guiding_curve', 'text_import_version']) expect(wire).not.toHaveProperty(key);
  expect(wire.throat_ext_length).toBe(0);
  expect(lookup.morph.target_shape).toBe(2);
  expect(lookup.enclosure.depth).toBe(100);
  expect(serializeDesign(hydrateDesignDocument(wire))).toEqual(wire);
  expect(serializeDesign(mergeDesignForFamily(lookup, 'OSSE'))).not.toHaveProperty('lookup_profile');
  expect(designFilename('table.cfg', 'LOOKUP')).toBe('table.mwg');
});

it('registers the table for profile search and publishes its catalog contract', () => {
  const field = PARAMETER_REGISTRY.find((item) => item.id === 'lookup.profile')!;
  expect(field.section).toBe('Profile');
  expect(fieldIsVisible(field, designForFamily('LOOKUP'))).toBe(true);
  expect(fieldMatchesQuery(field, 'lookup')).toBe(true);
  const catalog = buildParameterCatalog();
  expect(catalog.design_families).toContain('LOOKUP');
  expect(catalog.parameters.find((item) => item.id === field.id)).toMatchObject({
    path: 'lookup_profile', kind: 'table', families: ['LOOKUP'],
    default_by_family: { LOOKUP: [[0, 12.7], [120, 140]] },
  });
});

it('starts from the hydrate defaults, whichever way the design was reached', () => {
  const switched = serializeDesign(mergeDesignForFamily(designForFamily('OSSE'), 'LOOKUP'));
  const hydrated = serializeDesign(hydrateDesignDocument(serializeDesign(designForFamily('LOOKUP'))));
  expect(switched.length_mode).toBe('profile');
  expect(switched.coverage_mode).toBe('auto');
  expect(switched).toEqual(hydrated);
});

describe('lookup CSV paste', () => {
  it('accepts any all-text first row as a header', () => {
    expect(parseLookupPaste('z (mm), r (mm)\n0,12.7\n120,140')).toEqual([[0, 12.7], [120, 140]]);
    expect(parseLookupPaste('Axial;Radius\n0;12.7\n120;140')).toEqual([[0, 12.7], [120, 140]]);
  });
  it('reports the line numbers of the pasted text, not of the filtered rows', () => {
    expect(() => parseLookupPaste('# note\n\nz,r\n0,12.7\n10,x\n120,140')).toThrow('Line 5 contains a non-numeric value.');
    expect(() => parseLookupPaste('# note\n0,12.7\n\n5,1,2')).toThrow('Line 4 must contain exactly z and r in mm.');
    expect(() => parseLookupPaste('0,1\n\n0,2')).toThrow('strictly increasing (line 3)');
  });
  it('warns, by original line, about radii at or below zero', () => {
    expect(lookupRadiusWarning([12.7, 140])).toBeUndefined();
    expect(lookupRadiusWarning([0, 5, -1])).toContain('points 1, 3 are zero or negative');
    expect(lookupRadiusWarning([5, 0], [4, 7])).toContain('line 7 is zero or negative');
  });
  it.each(['0,12.7\n120,140', 'z_mm,r_mm\n0,12.7\n120,140', '# mm\n0 12.7\n120 140', '0;12.7\n120;140'])('reads pairs in millimetres', (text) => {
    expect(parseLookupPaste(text)).toEqual([[0, 12.7], [120, 140]]);
  });
  it('preserves falling radii and negative coordinates allowed by the mesher', () => {
    expect(parseLookupPaste('-1,4\n0,-2')).toEqual([[-1, 4], [0, -2]]);
  });
  it.each(['0,1', '1,2\n0,3', '0,2\n0,3', '0,2,3\n1,3,4', '0,NaN\n1,3', '0,\n1,3'])('refuses invalid tables without sorting or dropping columns', (text) => {
    expect(() => parseLookupPaste(text)).toThrow();
  });
});

it('restores the LOOKUP table from autosave', () => {
  const records = new Map<string, string>();
  const storage = {
    getItem: (key: string) => records.get(key) ?? null,
    setItem: (key: string, value: string) => { records.set(key, value); },
    removeItem: (key: string) => { records.delete(key); },
  };
  const lookup = designForFamily('LOOKUP');
  useDesignStore.getState().replaceDocument(lookup);
  expect(writeAutosave(storage)).toBe(true);
  resetDesignStore();
  expect(restoreAutosave(storage)).toBe(true);
  expect(useDesignStore.getState().design).toEqual(lookup);
  resetDesignStore();
});
