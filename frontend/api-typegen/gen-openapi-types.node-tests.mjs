import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'wg-api-typegen-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const tool = join(root, 'frontend/api-typegen');
  await mkdir(tool, { recursive: true });
  await mkdir(join(root, 'docs/reference'), { recursive: true });
  await copyFile(new URL('./gen-openapi-types.mjs', import.meta.url), join(tool, 'gen-openapi-types.mjs'));
  await symlink(fileURLToPath(new URL('../..', import.meta.resolve('openapi-typescript'))), join(tool, 'node_modules'), 'dir');
  const schema = {
    openapi: '3.1.0', info: { title: 'Fixture', version: '1' }, paths: {},
    components: { schemas: { Example: {
      type: 'object', required: ['value'],
      properties: { value: { type: 'string' }, enabled: { type: 'boolean', default: false } },
    } } },
  };
  const input = join(root, 'docs/reference/openapi.v1.json');
  await writeFile(input, JSON.stringify(schema));
  const output = join(root, 'frontend/src/api/generated/openapi.ts');
  // Resolve inputs relative to the script, even when launched outside the frontend.
  const run = (...args) => spawnSync(process.execPath, [join(tool, 'gen-openapi-types.mjs'), ...args], {
    cwd: tmpdir(), encoding: 'utf8', timeout: 30_000,
  });
  return { input, output, run, schema };
}

test('generation is deterministic and schema defaults remain optional', async (t) => {
  const { output, run } = await fixture(t);
  assert.equal(run('--write').status, 0);
  const original = await readFile(output, 'utf8');
  assert.match(original, /value: string;/);
  assert.match(original, /enabled\?: boolean;/);
  assert.equal(run('--check').status, 0);
  assert.equal(run('--write').status, 0);
  assert.equal(await readFile(output, 'utf8'), original);
});

test('check rejects a missing output without writing it', async (t) => {
  const { output, run } = await fixture(t);
  const result = run('--check');
  assert.equal(result.status, 1);
  assert.match(result.stderr, /API types have drifted or are missing/);
  await assert.rejects(readFile(output), { code: 'ENOENT' });
});

test('check rejects a modified output without repairing it', async (t) => {
  const { output, run } = await fixture(t);
  assert.equal(run('--write').status, 0);
  const changed = (await readFile(output, 'utf8')).replace('value: string;', 'value: number;');
  await writeFile(output, changed);
  assert.equal(run('--check').status, 1);
  assert.equal(await readFile(output, 'utf8'), changed);
});

test('check detects a schema mutation and regeneration repairs the drift', async (t) => {
  const { input, output, run, schema } = await fixture(t);
  assert.equal(run('--write').status, 0);
  const original = await readFile(output, 'utf8');
  schema.components.schemas.Example.properties.value.type = 'number';
  await writeFile(input, JSON.stringify(schema));
  assert.equal(run('--check').status, 1);
  assert.equal(await readFile(output, 'utf8'), original);
  assert.equal(run('--write').status, 0);
  assert.match(await readFile(output, 'utf8'), /value: number;/);
  assert.equal(run('--check').status, 0);
});

test('embedded export-member definitions resolve without changing the input', async (t) => {
  const { input, output, run, schema } = await fixture(t);
  schema.paths['/api/workspace/write-export'] = { post: {
    responses: { 200: { description: 'OK' } },
    requestBody: { required: true, content: { 'application/json': { schema: {
      type: 'object', required: ['members'],
      $defs: { ExportMember: {
        type: 'object', required: ['relative_path'],
        properties: { relative_path: { type: 'string' }, text: { type: 'string' } },
      } },
      properties: { members: { type: 'array', items: { $ref: '#/$defs/ExportMember' } } },
    } } } },
  } };
  const original = JSON.stringify(schema);
  await writeFile(input, original);
  const result = run('--write');
  assert.equal(result.status, 0, result.stderr);
  const rendered = await readFile(output, 'utf8');
  assert.match(rendered, /relative_path: string;/);
  assert.match(rendered, /text\?: string;/);
  assert.doesNotMatch(rendered, /ExportMember/);
  assert.equal(await readFile(input, 'utf8'), original);
  assert.equal(run('--check').status, 0);
});
