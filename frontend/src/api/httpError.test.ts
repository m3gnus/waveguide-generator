import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { failureWords, formatApiDetail, httpErrorMessage, fieldSentence, keepRefusalOriginals, LOST_CONTACT, nameFieldsWith, PLAIN_REQUEST_FAILURE, PLAIN_VALIDATION_FAILURE, ProgramFault, readsPlain, refusalText, rowSentence, validationSentence } from './httpError';

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
    expect(refusalText([{ loc: ['body'], msg: 'Field required' }], 'test')).toBe(PLAIN_VALIDATION_FAILURE);
    expect(refusalText(null, 'test')).toBe(PLAIN_REQUEST_FAILURE);
    expect(kept[0]).toBe('body: Field required');
    keepRefusalOriginals(() => {});
  });

  it('names the control, never the raw key', () => {
    nameFieldsWith(path => path === 'a' ? { label: 'Mouth coverage', place: 'the Profile section' } : path === 'mesh.q' ? { label: 'Mesh quality' } : null);
    expect(refusalText([{ loc: ['design', 'a'], msg: 'Input should be less than 80' }], 'test')).toBe('Mouth coverage must be less than 80. Change it in the Profile section.');
    expect(refusalText([{ loc: ['body', 'design', 'mesh', 'q'], msg: 'Input should be greater than or equal to 0.5' }], 'test')).toBe('Mesh quality must be greater than or equal to 0.5. Change it.');
    expect(refusalText([{ loc: ['body', 'design', 'a'], msg: 'Field required' }], 'test')).toBe('Mouth coverage is empty. Enter a value in the Profile section.');
    expect(refusalText([{ loc: ['design', 'a'], msg: 'Input should be a valid number' }], 'test')).toBe('WG cannot use the value of Mouth coverage. Enter a new value in the Profile section.');
    expect(refusalText([{ loc: ['design', 'unknown_key'], msg: 'Field required' }], 'test')).toBe(PLAIN_VALIDATION_FAILURE);
    nameFieldsWith(() => null);
  });

  it('drops a wire code from the front of a sentence it can show', () => {
    expect(refusalText('polar_grid_narrowing: Turn it on under Directivity map.', 'test')).toBe('Turn it on under Directivity map.');
    expect(failureWords(new Error('imported_axial_legacy_retry: Start a new solve.'))).toBe('Start a new solve.');
  });

  it('treats a server sentence with internal words as the program\'s, and keeps the original', () => {
    const kept: string[] = [];
    keepRefusalOriginals(({ message }) => kept.push(message));
    expect(refusalText('role resolution: source hf has no geometric throat candidate', 'test')).toBe(PLAIN_REQUEST_FAILURE);
    expect(refusalText('Unknown ingestion record wgi_123', 'test', 'Send it again.')).toBe('Send it again.');
    expect(refusalText({ code: 'x', message: 'This model is too large.', next_step: 'Use a coarser mesh.' }, 'test')).toBe('This model is too large. Use a coarser mesh.');
    expect(kept).toEqual(['role resolution: source hf has no geometric throat candidate', 'Unknown ingestion record wgi_123']);
    keepRefusalOriginals(() => {});
  });

  it('tells a validator sentence from the framework', () => {
    expect(validationSentence('Value error, must be finite')).toBe('must be finite');
    expect(validationSentence('Input should be less than 10')).toBeNull();
    expect(validationSentence('Self-intersecting mouth.')).toBe('Self-intersecting mouth.');
    expect(fieldSentence(['body'], 'Input should be less than 10')).toBeNull();
  });

  it('says what to do to a value beside its own control', () => {
    expect(rowSentence('Field required')).toBe('Enter a value.');
    expect(rowSentence('Input should be less than 80')).toBe('Enter a value less than 80.');
    expect(rowSentence('Input should be a valid number')).toBe('Enter a value WG can use here.');
    expect(rowSentence('Value error, Coverage must exceed 10')).toBe('Coverage must exceed 10');
  });
});

describe('failureWords', () => {
  it('shows a plain sentence and hides what the program caused', () => {
    const kept: string[] = [];
    keepRefusalOriginals(({ message }) => kept.push(message));
    expect(failureWords(new Error('Close the opening.'))).toBe('Close the opening.');
    expect(failureWords(new Error('Export file already exists: /runs/my_horn/run_1.csv'))).toContain('run_1.csv');
    expect(failureWords(new ProgramFault('Field-plane header length must be greater than zero'), 'Select the run again.')).toBe('Select the run again.');
    expect(failureWords(new TypeError("Cannot read properties of undefined (reading 'id')"))).toBe(PLAIN_REQUEST_FAILURE);
    expect(failureWords(new Error('Request failed: 500 Internal Server Error'))).toBe(PLAIN_REQUEST_FAILURE);
    expect(failureWords(new Error('Saved setup unavailable (503).'))).toBe(PLAIN_REQUEST_FAILURE);
    expect(kept).toHaveLength(4);
    keepRefusalOriginals(() => {});
  });

  it('says a lost connection is one', () => {
    expect(failureWords(new TypeError('Failed to fetch'))).toBe(LOST_CONTACT);
    expect(failureWords(new TypeError('Load failed'))).toBe(LOST_CONTACT);
  });

  it('keeps lengths in millimetres and the user\'s own names', () => {
    expect(readsPlain('The 500 mm mouth does not fit (400 mm).')).toBe(true);
    expect(readsPlain('Open “my_horn” again.')).toBe(true);
    expect(readsPlain('Throat.Diameter must be a positive finite number')).toBe(false);
    expect(readsPlain("Review the preparation's findings before solving: finding-symmetry-cut-unverified-aeb61951b986acd4")).toBe(false);
    expect(readsPlain('Reduce Wall thickness. Then press Solve.')).toBe(true);
  });
});

describe('httpErrorMessage', () => {
  it('shows the server sentence, else the caller\'s, and never the status', async () => {
    await expect(httpErrorMessage(Response.json({ detail: 'Close the opening.' }, { status: 422 }), 'Failed.')).resolves.toBe('Close the opening.');
    await expect(httpErrorMessage(Response.json({ detail: '' }, { status: 422 }), 'Failed.')).resolves.toBe('Failed.');
    await expect(httpErrorMessage(Response.json({ detail: [{ msg: 'invalid' }] }, { status: 422 }), 'Failed.')).resolves.toBe('invalid');
    await expect(httpErrorMessage(Response.json({}, { status: 500 }), 'Failed.')).resolves.toBe('Failed.');
  });

  it('consumes the body once and falls back in words for invalid JSON', async () => {
    const response = new Response('unreadable', { status: 503 });
    await expect(httpErrorMessage(response, 'Unavailable.')).resolves.toBe('Unavailable.');
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
  it('keeps only activity tracking, the HTTP helper and existing design serializer as runtime imports', () => {
    // Serialization still depends on the design store. This guard removes the
    // jobs hop and viewport construction; activity tracking is transport-level
    // cancellation and does not claim store independence.
    const runtimeImports = imports('./solverMesh.ts').filter(({ bindings }) => !bindings.startsWith('type '));
    expect(runtimeImports.map(({ owner }) => owner).sort()).toEqual(['../shell/activityOperation', '../stores/design', './httpError']);
    expect(source('./solverMesh.ts')).not.toMatch(/\bsolverMeshScene\b|\bparseMSH\b|\bcreateImportedMeshScene\b/);
    expect(source('./solverMesh.ts')).not.toMatch(/\b(?:import|require)\s*\(/);
  });
});


describe('typed backend refusals', () => {
  it('shows the diagnosed problem and next step without rendering the field key', () => {
    expect(refusalText({ field: 'mesh.wall_thickness', user_message: 'The outer wall folds over itself at a tight bend.', next_step: 'Reduce Wall thickness.' }, 'mesh')).toBe('The outer wall folds over itself at a tight bend. Reduce Wall thickness.');
    expect(refusalText({ field: 'ATH text', user_message: '“Mesh.Enclosure.Dim” needs four positive distances.', next_step: 'Correct “Mesh.Enclosure.Dim” and import again.' }, 'import')).toContain('four positive distances');
    expect(refusalText({ field: 'mesh', user_message: 'stage 7 meshing: bad mesh', next_step: 'Retry.' }, 'mesh')).toBe(PLAIN_REQUEST_FAILURE);
  });

  it('names solve controls even before the parameter registry is loaded', () => {
    expect(fieldSentence(['body', 'options', 'num_frequencies'], 'Input should be greater than or equal to 1')).toBe('Frequency samples must be greater than or equal to 1. Change it in Solve options.');
    expect(fieldSentence(['options', 'frequencies_hz', 2], 'Input should be a valid number')).toBe('WG cannot use the value of Solver frequencies in Hz. Enter a new value in Solve options.');
  });
});

it('names nested solve-option controls', () => {
  expect(fieldSentence(['body', 'options', 'ground_plane', 'height_m'], 'Input should be a valid number')).toBe('WG cannot use the value of Height above surface. Enter a new value in Ground plane under Solve options.');
  expect(fieldSentence(['options', 'polar_config', 'distance'], 'Input should be greater than or equal to 0.1')).toBe('Measurement distance must be greater than or equal to 0.1. Change it in Directivity map under Solve options.');
});

it('shows the typed remedy retained in a validation row context', () => {
  expect(refusalText([{ loc: ['body', 'design', 'SOURCE-DISK'], msg: 'Value error, outer_radius_mm must exceed radius_mm', ctx: { error: {
    field: 'Source body radius', user_message: 'The source rim is too small or too large.', next_step: 'Set Source body radius at least 0.1 mm above Source disk radius and no greater than 500 mm.',
  } } }], 'mesh preview')).toBe('The source rim is too small or too large. Set Source body radius at least 0.1 mm above Source disk radius and no greater than 500 mm.');
});
