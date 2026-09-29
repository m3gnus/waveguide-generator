import { mkdir, readFile, writeFile } from 'node:fs/promises';
import openapiTS, { astToString } from 'openapi-typescript';

const schema = new URL('../../docs/reference/openapi.v1.json', import.meta.url);
const output = new URL('../src/api/generated/openapi.ts', import.meta.url);
const header = '// Generated from docs/reference/openapi.v1.json.\n'
  + '// Run npm --prefix frontend run generate:api-types to update.\n'
  + '// Do not edit by hand.\n\n';

const [mode, ...extra] = process.argv.slice(2);
if (!['--write', '--check'].includes(mode) || extra.length) {
  console.error('Usage: node gen-openapi-types.mjs --write|--check');
  process.exitCode = 2;
} else {
  const document = JSON.parse(await readFile(schema, 'utf8'));
  // This embedded request schema uses a schema-local $defs reference. Resolve
  // it before document-wide OpenAPI bundling, without changing the snapshot.
  const exportSchema = document.paths['/api/workspace/write-export']?.post
    ?.requestBody?.content?.['application/json']?.schema;
  if (exportSchema?.properties?.members?.items?.$ref === '#/$defs/ExportMember'
    && exportSchema.$defs?.ExportMember) {
    exportSchema.properties.members.items = exportSchema.$defs.ExportMember;
    delete exportSchema.$defs;
  }
  // Defaults describe server behavior; only `required` makes a wire field mandatory.
  const rendered = header + astToString(await openapiTS(document, { defaultNonNullable: false }));
  if (mode === '--write') {
    await mkdir(new URL('.', output), { recursive: true });
    await writeFile(output, rendered, 'utf8');
  } else {
    let current;
    try {
      current = await readFile(output, 'utf8');
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    if (current !== rendered) {
      console.error('API types have drifted or are missing; run npm --prefix frontend run generate:api-types');
      process.exitCode = 1;
    }
  }
}
