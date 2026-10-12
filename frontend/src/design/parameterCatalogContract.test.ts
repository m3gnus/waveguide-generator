import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { PARAMETER_REGISTRY } from './parameterRegistry';
import { buildParameterCatalog } from './parameterCatalogWire';

describe('public parameter catalog', () => {
  it('publishes family-specific defaults without inventing search ranges', () => {
    const catalog = buildParameterCatalog();
    const rosseR = catalog.parameters.find((field) => field.id === 'rosse.R');
    const scale = catalog.parameters.find((field) => field.id === 'common.scale');

    expect(rosseR?.default_by_family).toEqual({ 'R-OSSE': 140 });
    expect(scale?.default_by_family).toEqual({
      'R-OSSE': 1,
      OSSE: 1,
      ICW: 1,
      FREEFORM: 1,
    });
  });

  it('publishes a held-only option as current_only so catalog consumers do not offer it fresh', () => {
    const convention = buildParameterCatalog().parameters.find((field) => field.id === 'source.velocity_convention');
    expect(convention?.options.map((option) => [option.value, option.current_only])).toEqual([
      ['normal', false], ['axial', false], ['legacy', true],
    ]);
  });

  it('matches the committed server-owned integration artifact', () => {
    const bytes = readFileSync('../server/integration/parameter-catalog.v1.json');
    const committed = JSON.parse(new TextDecoder().decode(bytes));
    expect(buildParameterCatalog()).toEqual(committed);
  });

  it('keeps versioned catalog sections stable while the editor uses the new layout', () => {
    const catalog = buildParameterCatalog();
    for (const [id, oldSection, newSection] of [
      ['source.shape', 'Source Definition', 'Throat'],
      ['enclosure.front_resolution', 'Wall & Enclosure', 'Mesh'],
      ['mesh.vertical_offset', 'Solve & export mesh', 'Export'],
    ]) {
      const wire = catalog.parameters.find((field) => field.id === id)!;
      const editor = PARAMETER_REGISTRY.find((field) => field.id === id)!;
      expect(wire.section).toBe(oldSection);
      expect(editor.section).toBe(newSection);
      expect(editor.path).toBe(wire.path);
      expect(editor.kind).toBe(wire.kind);
      expect(editor.families).toEqual(wire.families);
    }
  });

  it('keeps v1 editor bounds non-null when a bounds object is present', () => {
    for (const field of buildParameterCatalog().parameters) {
      if (field.editor_bounds) {
        expect(Number.isFinite(field.editor_bounds.minimum)).toBe(true);
        expect(Number.isFinite(field.editor_bounds.maximum)).toBe(true);
      }
    }
  });
});
