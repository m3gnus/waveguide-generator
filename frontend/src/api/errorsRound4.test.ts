import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import cases from '../../../server/tests/fixtures/error_routes_round4.json';
import { refusalText } from './httpError';
import { solveFailureWords } from '../model/currentModel';

it.each(cases)('keeps the final text for $label', row => {
  const words = row.solve ? solveFailureWords(String(row.detail)) : refusalText(row.detail, row.label);
  expect(words).toBe(row.expected);
});
it('uses native file URL conversion in the catalog contract on Windows', () => {
  const readSource = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url))).toString();
  const source = readSource('../design/refusalCatalog.test.ts');
  expect(source).toContain('fileURLToPath(new URL(file, import.meta.url))');
  expect(source).not.toContain('.pathname');
});
