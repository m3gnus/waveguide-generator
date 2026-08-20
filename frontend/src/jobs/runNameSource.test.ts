import { describe, expect, it } from 'vitest';
import type { CadReturnBundle } from '../api/cadlink';
import { cadRunName, runNameSourceFor } from './runNameSource';

const BUNDLE = {
  name: 'Tritonia V-req7.wgreturn', bundlePath: '/cad/Tritonia V-req7.wgreturn',
  modifiedAt: '2026-08-19T12:00:00Z', readable: true, documentName: 'Tritonia V',
  requestId: 'req7', sourceCount: 2, instanceCount: 2, sources: [],
} satisfies CadReturnBundle;

describe('runNameSourceFor', () => {
  it('names a parametric run from the design', () => {
    expect(runNameSourceFor('parametric', BUNDLE, 'ATH Tritonia-M'))
      .toEqual({ name: 'ATH Tritonia-M', origin: 'design' });
  });

  it('names a CAD run from the Fusion document, not the design left open behind it', () => {
    // The bug this replaces: a Fusion return filed under whichever .cfg the
    // autosave draft happened to restore.
    expect(runNameSourceFor('cad', BUNDLE, 'ATH Tritonia-M'))
      .toEqual({ name: 'Tritonia V', origin: 'cad' });
  });

  it('leaves a CAD run unnamed rather than borrowing the design name', () => {
    expect(runNameSourceFor('cad', null, 'ATH Tritonia-M')).toEqual({ name: '', origin: 'cad' });
  });

  it('falls back to the bundle name only when the manifest could not be read', () => {
    expect(cadRunName({ ...BUNDLE, readable: false, documentName: null })).toBe('Tritonia V-req7.wgreturn');
  });

  it('normalizes a document name that could not survive a filename or a config value', () => {
    expect(cadRunName({ ...BUNDLE, documentName: '  "Horn"; {v2}  ' })).toBe('Horn v2');
  });
});
