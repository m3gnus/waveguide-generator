import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { formatApiDetail, httpErrorMessage, keepRefusalOriginals, PLAIN_REQUEST_FAILURE, PLAIN_VALIDATION_FAILURE, refusalText, validationSentence } from './httpError';

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

describe('refusalText', () => {
  it('keeps a sentence the server worded', () => {
    expect(refusalText('Close the opening.', 'test')).toBe('Close the opening.');
    expect(refusalText([{ loc: ['body', 'design'], msg: 'Value error, Solve it from Sources and baffle.' }], 'test')).toBe('Solve it from Sources and baffle.');
  });

  it('turns framework validation and bare failures into one plain message, and keeps the original', () => {
    const kept: string[] = [];
    keepRefusalOriginals(({ message }) => kept.push(message));
    expect(refusalText([{ loc: ['body', 'design', 'a'], msg: 'Field required' }], 'test')).toBe(PLAIN_VALIDATION_FAILURE);
    expect(refusalText(null, 'test')).toBe(PLAIN_REQUEST_FAILURE);
    expect(kept[0]).toBe('body.design.a: Field required');
    keepRefusalOriginals(() => {});
  });

  it('tells a validator sentence from the framework', () => {
    expect(validationSentence('Value error, must be finite')).toBe('must be finite');
    expect(validationSentence('Input should be less than 10')).toBeNull();
    expect(validationSentence('Self-intersecting mouth.')).toBe('Self-intersecting mouth.');
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
    expect(source('./httpError.ts')).not.toMatch(/\b(?:import|require)\s*\(/);
  });

  it.each(['designIo', 'importedModels', 'meshReplay', 'solverMesh'])('%s imports the formatter from its HTTP owner', name => {
    const formatterImports = imports(`./${name}.ts`).filter(({ bindings }) => /\b(?:formatApiDetail|refusalText)\b/.test(bindings));
    expect(formatterImports.map(({ owner }) => owner)).toEqual(['./httpError']);
    expect(imports(`./${name}.ts`).filter(({ bindings, owner }) =>
      owner === '../jobs/actions' && !bindings.startsWith('type '))).toEqual([]);
  });
});

describe('solver mesh transport boundary', () => {
  it('keeps only the HTTP helper and existing design serializer as runtime imports', () => {
    // Serialization still depends on the design store. This guard removes the
    // jobs hop and viewport construction; it does not claim store independence.
    const runtimeImports = imports('./solverMesh.ts').filter(({ bindings }) => !bindings.startsWith('type '));
    expect(runtimeImports.map(({ owner }) => owner).sort()).toEqual(['../stores/design', './httpError']);
    expect(source('./solverMesh.ts')).not.toMatch(/\bsolverMeshScene\b|\bparseMSH\b|\bcreateImportedMeshScene\b/);
    expect(source('./solverMesh.ts')).not.toMatch(/\b(?:import|require)\s*\(/);
  });
});
