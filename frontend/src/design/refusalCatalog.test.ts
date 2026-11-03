import { presetLabels } from '../sketch/adapters/sourcePreset';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { PARAMETER_SECTION_DEFINITIONS, refusalControlCatalog } from './parameterRegistry';

// Editor controls keep their labels in their existing JSX owner. Extract the
// literal labels for the backend catalog without adding a runtime UI dependency.
const editors = ['SourceContourEditor.tsx', 'SourceAssemblyEditor.tsx', 'FreeformEditors.tsx', 'FrontProperties.tsx', 'ParamPanel.tsx'];
const labels = new Set<string>();
for (const file of editors) {
  const source = readFileSync(fileURLToPath(new URL(file, import.meta.url)).replace(/^\/@fs/, '')).toString();
  for (const match of source.matchAll(/(?:label|aria-label)="([^"]+)"|<(?:h[23]|th|label|summary)[^>]*>([^<{}]+)</g)) { const label = (match[1] || match[2]).trim(); if (label) labels.add(label); }
  const scalars = /const scalarLabels = \{([^}]+)\}/.exec(source)?.[1] || '';
  for (const match of scalars.matchAll(/: '([^']+)'/g)) labels.add(match[1]);
}
const catalog = refusalControlCatalog(presetLabels, [...labels].sort());
it('exports the UI-owned names for the installed-mesher contract test', () => {
  const filename = (globalThis as { process?: { env: Record<string, string> } }).process?.env.CONTROL_CATALOG_OUTPUT;
  if (filename) writeFileSync(filename, JSON.stringify(catalog, null, 2) + '\n');
  expect(Object.values(catalog).every(control => control.label.trim().length > 0)).toBe(true);
  expect(catalog['options.engine'].label).toBe('Solver');
});

it('keeps the pinned Drive catalog identity while displaying Drivers', () => {
  expect(PARAMETER_SECTION_DEFINITIONS.some(section => section.title === 'Drivers')).toBe(true);
  expect(catalog['editor.Drive']).toEqual({ legacyKey: 'Drive', label: 'Drive' });
  expect(catalog).not.toHaveProperty('section.Drivers');
  expect(PARAMETER_SECTION_DEFINITIONS.some(section => section.title === 'Crossover')).toBe(true);
  expect(catalog).not.toHaveProperty('editor.Crossover');
  expect(catalog).not.toHaveProperty('section.Crossover');
});
