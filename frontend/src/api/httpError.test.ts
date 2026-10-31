import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { formatApiDetail, httpErrorMessage } from './httpError';

const source = (path: string) => readFileSync(new URL(path, import.meta.url).pathname).toString();
const imports = (path: string) => [...source(path).matchAll(/^import\s+([\s\S]+?)\s+from\s+['"](.+?)['"];?$/gm)]
  .map(([, bindings, owner]) => ({ bindings, owner }));

describe('formatApiDetail', () => {
  it.each([
    ['legacy detail', 'legacy detail'],
    ['', ''],
    [[{ loc: ['body', 'design', 0], msg: 'Value error, must be finite', message: 'ignored' }], 'body.design.0: Value error, must be finite'],
    [{ message: 'refused', code: 'not_used' }, 'refused'],
    [{ design: [{ msg: 'invalid' }, null, { message: 'missing' }], retry: false }, 'design: invalid; missing; retry: false'],
    [[null, undefined, '', [], {}, 0, true], '0; true'],
    [null, null],
    [undefined, null],
    [{}, null],
    [[], null],
  ])('preserves detail %j as %j', (detail, message) => {
    expect(formatApiDetail(detail)).toBe(message);
  });
});

describe('httpErrorMessage', () => {
  it('keeps its existing string-only policy, including empty strings', async () => {
    await expect(httpErrorMessage(Response.json({ detail: '' }, { status: 422 }), 'Failed')).resolves.toBe('');
    await expect(httpErrorMessage(Response.json({ detail: [{ msg: 'invalid' }] }, { status: 422 }), 'Failed'))
      .resolves.toBe('Failed (422)');
  });

  it('consumes the body once and retains the status fallback for invalid JSON', async () => {
    const response = new Response('unreadable', { status: 503 });
    await expect(httpErrorMessage(response, 'Unavailable')).resolves.toBe('Unavailable (503)');
    expect(response.bodyUsed).toBe(true);
  });
});

describe('HTTP detail helper ownership', () => {
  it('keeps the formatter owner free of runtime dependencies', () => {
    expect(imports('./httpError.ts')).toEqual([]);
  });

  it.each(['designIo', 'importedModels', 'meshReplay', 'solverMesh'])('%s imports the formatter from its HTTP owner', name => {
    const formatterImports = imports(`./${name}.ts`).filter(({ bindings }) => /\bformatApiDetail\b/.test(bindings));
    expect(formatterImports.map(({ owner }) => owner)).toEqual(['./httpError']);
  });
});
