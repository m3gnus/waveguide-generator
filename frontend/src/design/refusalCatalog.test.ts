import { presetLabels } from '../sketch/adapters/sourcePreset';
import { readFileSync, writeFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { refusalControlCatalog, SOLVE_CONTROLS } from './parameterRegistry';

const catalog = refusalControlCatalog(presetLabels);
const filename = new URL('../../../server/design/control_labels.json', import.meta.url).pathname.replace(/^\/@fs/, '');
it('keeps backend control names generated from the UI owner', () => {
  if ((globalThis as { process?: { env: Record<string, string> } }).process?.env.UPDATE_CONTROL_LABELS === '1') writeFileSync(filename, JSON.stringify(catalog, null, 2) + '\n');
  expect(JSON.parse(readFileSync(filename).toString())).toEqual(catalog);
});
it('keeps early solve-validation names generated from the same owner', () => {
  const filename = new URL('../api/' + 'httpError.ts', import.meta.url).pathname.replace(/^\/@fs/, '');
  const source = readFileSync(filename).toString();
  const pattern = /const solveControlLabels: Record<string, NamedControl> = ([\s\S]*?);\n\/\/ END GENERATED SOLVE CONTROL LABELS/;
  if ((globalThis as { process?: { env: Record<string, string> } }).process?.env.UPDATE_CONTROL_LABELS === '1') writeFileSync(filename, source.replace(pattern, `const solveControlLabels: Record<string, NamedControl> = ${JSON.stringify(SOLVE_CONTROLS, null, 2)};\n// END GENERATED SOLVE CONTROL LABELS`));
  expect(JSON.parse(pattern.exec(readFileSync(filename).toString())![1])).toEqual(SOLVE_CONTROLS);
});
