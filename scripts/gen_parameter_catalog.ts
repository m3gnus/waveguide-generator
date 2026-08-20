import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { buildParameterCatalog } from '../frontend/src/design/parameterCatalogWire';

const output = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../server/integration/parameter-catalog.v1.json',
);
const rendered = `${JSON.stringify(buildParameterCatalog(), null, 2)}\n`;

if (process.argv.includes('--check')) {
  const current = readFileSync(output, 'utf8');
  if (current !== rendered) {
    console.error('Parameter catalog is stale. Run scripts/gen_parameter_catalog.ts --write.');
    process.exitCode = 1;
  }
} else if (process.argv.includes('--write')) {
  writeFileSync(output, rendered, 'utf8');
} else {
  process.stdout.write(rendered);
}
