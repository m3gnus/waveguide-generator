import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

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

  it('matches the committed server-owned integration artifact', () => {
    const bytes = readFileSync('../server/integration/parameter-catalog.v1.json');
    const committed = JSON.parse(new TextDecoder().decode(bytes));
    expect(buildParameterCatalog()).toEqual(committed);
  });
});
