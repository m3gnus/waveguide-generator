import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { buildParameterCatalog } from './parameterCatalogWire';

describe('public parameter catalog', () => {
  it('matches the committed server-owned integration artifact', () => {
    const bytes = readFileSync('../server/integration/parameter-catalog.v1.json');
    const committed = JSON.parse(new TextDecoder().decode(bytes));
    expect(buildParameterCatalog()).toEqual(committed);
  });
});
