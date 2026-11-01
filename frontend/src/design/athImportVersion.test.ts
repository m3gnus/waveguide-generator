import { describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { mergeDesignForFamily, serializeDesign, type DesignFamily } from '../stores/design';
import { isCurrentAthImport } from './athImportControls';

describe('ATH import interpretation', () => {
  it.each(['ath-2026-08c-v1', 'ath-2026-09a-v2', 'ath-2026-09a-v3'])('keeps %s through reopen and save', (version) => {
    const wire = {
      formula: 'R-OSSE', text_import_version: version, enclosure: null,
      ...(version.endsWith('v3') ? { ath_axial_scale: 2, rotation: { raw: '10+sin(p)' }, mesh: { ath_throat_ext_segments: 4 } } : {}),
    };
    const design = hydrateDesignDocument(wire);
    const saved = serializeDesign(design);
    expect(saved.text_import_version).toBe(version);
    expect(isCurrentAthImport(design)).toBe(!version.endsWith('v1'));
    expect(serializeDesign(hydrateDesignDocument(saved))).toEqual(saved);
    if (version.endsWith('v3')) {
      expect(saved.ath_axial_scale).toBe(2);
      expect(saved.rotation).toEqual({ raw: '10+sin(p)' });
      expect((saved.mesh as Record<string, unknown>).ath_throat_ext_segments).toBe(4);
    }
  });
});


it.each<DesignFamily>(['ICW', 'FREEFORM', 'LOOKUP', 'OSSE-NATIVE'])('clears unsupported ATH controls when switching to %s', (formula) => {
  const design = hydrateDesignDocument({
    formula: 'R-OSSE', text_import_version: 'ath-2026-09a-v3', ath_axial_scale: 2,
    rotation: { raw: '10+sin(p)' }, mesh: { ath_throat_ext_segments: 4 },
  });
  const saved = serializeDesign(mergeDesignForFamily(design, formula));
  expect(saved).not.toHaveProperty('ath_axial_scale');
  expect(saved).not.toHaveProperty('rotation');
  expect(saved.mesh).not.toHaveProperty('ath_throat_ext_segments');
});
